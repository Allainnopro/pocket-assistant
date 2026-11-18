"""What every API route shares: the context, a database connection, and who is calling.

Two kinds of secret, both sent as `Authorization: Bearer <secret>`:
- the launch password, new at each launch, for this PC's own app. It works only on
  127.0.0.1 and never on a request `tailscale serve` forwarded: a password that leaked
  to another device still opens nothing from there;
- a device key, for a paired device (engine/devices.py).

`tailscale serve` removes any Tailscale-User-Login a client sends and adds the real
one, but only for requests from other devices on the tailnet. A local process can
forge the header, so it is a second check on top of the secret, never instead of it.
"""

from __future__ import annotations

import hmac
import threading
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status

from ..config import Config
from ..costs import TIMEZONE
from ..links import ADDRESS
from ..pipeline import utc_now
from ..store import Store
from .devices import device_for_key
from .events import EventBus
from .jobs import ProfileHolder


@dataclass(frozen=True)
class ApiContext:
    cfg: Config
    holder: ProfileHolder
    bus: EventBus
    launch_password: str
    stop: threading.Event
    dev: bool = False
    clock: Callable[[], datetime] = utc_now


@dataclass(frozen=True)
class Caller:
    kind: Literal["local", "device"]
    device_id: int | None = None


def local_host(ctx: ApiContext) -> str:
    return f"127.0.0.1:{ctx.cfg.api.port}"


def context(request: Request) -> ApiContext:
    return request.app.state.ctx


Ctx = Annotated[ApiContext, Depends(context)]


def open_store(ctx: Ctx) -> Iterator[Store]:
    """One connection per request. FastAPI may enter this dependency and run the route
    on different worker threads, but only one of them uses the connection at a time."""
    store = Store(ctx.cfg.db_path, any_thread=True)
    # SQLite's own LIKE ignores case for ASCII only: "dai hoc" works, "đại học" for "Đại học" doesn't
    store.conn.create_function("fold", 1, lambda text: fold_text(text) if text else text, deterministic=True)
    try:
        yield store
    finally:
        store.close()


Db = Annotated[Store, Depends(open_store)]


def _refused() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "A valid password or device key is needed.",
                         headers={"WWW-Authenticate": "Bearer"})


def _login_in(header: str) -> str:
    """Tailscale writes a login with non-ASCII characters in RFC 2047 form."""
    try:
        return str(make_header(decode_header(header))).casefold()
    except (HeaderParseError, LookupError, ValueError):
        return header.casefold()


def caller(request: Request, ctx: Ctx, db: Db) -> Caller:
    scheme, _, secret = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secret:
        raise _refused()
    # tailscale serve passes a client's Host through unchanged, so a tailnet device can name
    # 127.0.0.1 too; but serve always sets these headers, tagged devices included
    via_tailscale = "x-forwarded-for" in request.headers or "x-forwarded-host" in request.headers
    is_local = (not via_tailscale and request.headers.get("host") == local_host(ctx)
                and hmac.compare_digest(secret.encode(), ctx.launch_password.encode()))
    if is_local:
        who = Caller("local")
    else:
        device_id = device_for_key(db, secret, ctx.clock())
        if device_id is None:
            raise _refused()
        who = Caller("device", device_id)
    owner_login(request, ctx)  # after the secret: a wrong one is a 401 whoever sends it
    return who


def owner_login(request: Request, ctx: Ctx) -> None:
    """With [api] tailnet_login set, a request carrying Tailscale's login header must be the
    owner's. Also on /api/pair, so a stranger on the tailnet can't burn the owner's code."""
    login, owner = request.headers.get("tailscale-user-login"), ctx.cfg.api.tailnet_login
    if owner and login is not None and _login_in(login) != owner.casefold():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This Tailscale account isn't the owner's.")


def local_caller(who: Annotated[Caller, Depends(caller)]) -> Caller:
    if who.kind != "local":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the main computer can do this.")
    return who


Authorized = Annotated[Caller, Depends(caller)]
LocalOnly = Annotated[Caller, Depends(local_caller)]
OwnerLogin = Annotated[None, Depends(owner_login)]


def utc_text(day: date) -> str:
    """The UTC moment HCMC's `day` starts, as SQLite writes datetime('now')."""
    start = datetime.combine(day, time(), tzinfo=TIMEZONE).astimezone(timezone.utc)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def today_in_hcmc(ctx: ApiContext) -> date:
    return ctx.clock().astimezone(TIMEZONE).date()


def fold_text(text: str) -> str:
    """Case folded, in one Unicode form: Vietnamese arrives precomposed or decomposed
    (Unikey's "Unicode tổ hợp"), and the two look the same but compare unequal."""
    return unicodedata.normalize("NFC", text.casefold())


def like(text: str) -> str:
    """A LIKE pattern that finds the text literally, against fold(column), with ESCAPE '\\'."""
    escaped = fold_text(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def sender_name(sender: str, domain: str) -> str:
    """A display name, or the domain. Never an address: university mail comes from classmates."""
    name = ADDRESS.sub("", parseaddr(sender)[0]).strip(" <>()\"'")
    return name or domain


def gmail_link(address: str | None, message_id: str) -> str:
    """authuser opens the right account when several are signed in."""
    account = f"?authuser={quote(address, safe='')}" if address else ""
    return f"https://mail.google.com/mail/{account}#all/{quote(message_id, safe='')}"
