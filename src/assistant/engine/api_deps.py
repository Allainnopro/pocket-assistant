"""What every API route shares: the context, a database connection, and who is calling.

Two kinds of secret, both sent as `Authorization: Bearer <secret>`:
- the launch password, new at each launch, for this PC's own app. It works only on
  127.0.0.1: a password that leaked to another device still opens nothing from there;
- a device key, for a paired device (engine/devices.py).

`tailscale serve` removes any Tailscale-User-Login a client sends and adds the real
one, but only for requests from other devices on the tailnet. A local process can
forge the header, so it is a second check on top of the secret, never instead of it.
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status

from ..config import Config
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
    is_local = (request.headers.get("host") == local_host(ctx)
                and hmac.compare_digest(secret.encode(), ctx.launch_password.encode()))
    if is_local:
        who = Caller("local")
    else:
        device_id = device_for_key(db, secret, ctx.clock())
        if device_id is None:
            raise _refused()
        who = Caller("device", device_id)
    login, owner = request.headers.get("tailscale-user-login"), ctx.cfg.api.tailnet_login
    if owner and login is not None and _login_in(login) != owner.casefold():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This Tailscale account isn't the owner's.")
    return who


def local_caller(who: Annotated[Caller, Depends(caller)]) -> Caller:
    if who.kind != "local":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the main computer can do this.")
    return who


Authorized = Annotated[Caller, Depends(caller)]
LocalOnly = Annotated[Caller, Depends(local_caller)]
