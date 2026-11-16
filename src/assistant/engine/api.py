"""The local API: what the dashboard and the chibi talk to.

It listens on 127.0.0.1 only. Other devices reach it through `tailscale serve`, which
passes the PC's *.ts.net name as the Host header. Checks, outermost first:

1. Host: exactly 127.0.0.1:<port> or [api] tailnet_host. This stops DNS rebinding,
   where a web page's own domain is pointed at 127.0.0.1 to read this API.
2. CORS: only the Tauri window's origin (plus Vite's with --dev), so other web pages
   can't read the answers.
3. A secret on every route but /api/health and /api/pair (api_deps.py).

No documentation routes: nothing but this app has a reason to read them.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import __version__
from ..errors import PairingError
from . import api_emails, api_events, api_opportunities, api_overview
from .api_deps import ApiContext, Ctx, Db, LocalOnly, local_host
from .devices import list_devices, redeem_code, revoke

__all__ = ["ApiContext", "create_app"]

TAURI_ORIGIN = "http://tauri.localhost"  # Tauri 2's default webview origin on Windows
VITE_ORIGIN = "http://localhost:5173"


class HostCheck:
    """Exact, port included. (Starlette's TrustedHostMiddleware ignores ports.)"""

    def __init__(self, app: ASGIApp, allowed: frozenset[str]) -> None:
        self.app, self.allowed = app, allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            hosts = [value for name, value in scope["headers"] if name == b"host"]
            if len(hosts) != 1 or hosts[0].decode("latin-1").lower() not in self.allowed:
                await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class Health(BaseModel):
    ok: bool
    version: str


class PairRequest(BaseModel):
    code: str = Field(max_length=20)
    device_name: str = Field(default="", max_length=200)


class PairResponse(BaseModel):
    device_id: int
    key: str


class Device(BaseModel):
    id: int
    name: str
    created_at: str
    last_seen_at: str | None
    revoked_at: str | None


class DeviceList(BaseModel):
    devices: list[Device]


class Revoked(BaseModel):
    revoked: bool


def create_app(ctx: ApiContext) -> FastAPI:
    """Builds the app and reads nothing, so it can also produce the OpenAPI document."""
    app = FastAPI(title="Pocket Assistant", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.add_middleware(CORSMiddleware, allow_origins=[TAURI_ORIGIN] + ([VITE_ORIGIN] if ctx.dev else []),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    # Added last, so it runs first
    allowed = {local_host(ctx)} | ({ctx.cfg.api.tailnet_host} if ctx.cfg.api.tailnet_host else set())
    app.add_middleware(HostCheck, allowed=frozenset(allowed))

    @app.get("/api/health")
    def health() -> Health:
        return Health(ok=True, version=__version__)

    @app.post("/api/pair")
    def pair(body: PairRequest, ctx: Ctx, db: Db) -> PairResponse:
        try:
            device_id, key = redeem_code(db, body.code, body.device_name, ctx.clock())
        except PairingError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
        return PairResponse(device_id=device_id, key=key)

    @app.get("/api/devices")
    def devices(_: LocalOnly, db: Db) -> DeviceList:
        return DeviceList(devices=[Device(**dict(r)) for r in list_devices(db)])

    @app.delete("/api/devices/{device_id}")
    def revoke_device(device_id: int, _: LocalOnly, ctx: Ctx, db: Db) -> Revoked:
        if not revoke(db, device_id, ctx.clock()):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such device.")
        return Revoked(revoked=True)

    app.include_router(api_opportunities.router)
    app.include_router(api_emails.router)
    app.include_router(api_overview.router)
    app.include_router(api_events.router)
    return app
