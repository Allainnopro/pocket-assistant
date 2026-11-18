"""A local API on a temporary database, for the API tests. Import the fixtures you use."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_scheduler import FakeClock

from assistant.config import load_config
from assistant.engine.api import ApiContext, create_app
from assistant.engine.devices import new_pairing_code, redeem_code
from assistant.engine.events import EventBus
from assistant.engine.jobs import ProfileHolder
from assistant.store import Store

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)  # 10:00 in Ho Chi Minh City
PASSWORD = "launch-password-for-tests"
TAILNET = "pc.tail1234.ts.net"
TAILNET_CONFIG = f'[api]\ntailnet_host = "{TAILNET}"\ntailnet_login = "me@gmail.com"\n'


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """The app, a client that passes the Host check, and a Store on the same database."""

    def __init__(self, folder: Path, config: str = "", *, dev: bool = False, now: datetime = NOW) -> None:
        (folder / "config.toml").write_text(config, encoding="utf-8")
        shutil.copy(ROOT / "profile.example.toml", folder / "profile.toml")
        self.cfg = load_config(folder / "config.toml")
        self.bus = EventBus()
        self.clock = FakeClock(now)
        self.stop = threading.Event()
        self.ctx = ApiContext(cfg=self.cfg, holder=ProfileHolder.from_file(self.cfg.profile_path), bus=self.bus,
                              launch_password=PASSWORD, stop=self.stop, dev=dev, clock=self.clock)
        self.app = create_app(self.ctx)
        self.client = TestClient(self.app, base_url=f"http://127.0.0.1:{self.cfg.api.port}")
        self.store = Store(self.cfg.db_path)

    def pair(self, name: str = "laptop") -> tuple[int, str]:
        return redeem_code(self.store, new_pairing_code(self.store, self.clock()), name, self.clock())

    def get(self, path: str, **params):
        """As this PC's own app."""
        r = self.client.get(path, params=params, headers=bearer(PASSWORD))
        assert r.status_code == 200, r.text
        return r.json()

    def close(self) -> None:
        self.store.close()


def dummy_ctx() -> ApiContext:
    """Enough to build the app for its OpenAPI document: the constructor reads nothing."""
    from assistant.config import Config

    cfg = Config("newer_than:{days}d", 500, (), Path("unused.db"), 4000)
    return ApiContext(cfg=cfg, holder=None, bus=EventBus(), launch_password="unused", stop=threading.Event())


@pytest.fixture
def api(tmp_path):
    made = Api(tmp_path)
    yield made
    made.close()


@pytest.fixture
def tailnet_api(tmp_path):
    made = Api(tmp_path, TAILNET_CONFIG)
    yield made
    made.close()


@pytest.fixture
def live_api(tmp_path):
    """(Api, port) with the app on a real uvicorn: real sockets, real disconnects."""
    import uvicorn

    port = free_port()  # the config's port, so the Host check lets local requests in
    made = Api(tmp_path, f"[api]\nport = {port}\n")
    server = uvicorn.Server(uvicorn.Config(made.app, host="127.0.0.1", port=port, log_config=None,
                                           timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn didn't start"
        time.sleep(0.01)
    yield made, port
    made.stop.set()
    server.should_exit = True
    thread.join(5)
    made.close()


@pytest.fixture
def api_at(tmp_path):
    made = []

    def start(now: datetime) -> Api:
        made.append(Api(tmp_path, now=now))
        return made[-1]

    yield start
    for one in made:
        one.close()
