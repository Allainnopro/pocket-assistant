"""The API served on a real socket, the way the engine runs it.

Run: pytest
"""

import socket
import threading
import time

import httpx2
import pytest
from api_helpers import PASSWORD, Api, bearer

from assistant.engine.server import ApiServer
from assistant.errors import ConfigError


@pytest.fixture
def made(tmp_path):
    api = Api(tmp_path)
    yield api
    api.stop.set()
    api.close()


def host(api: Api) -> dict[str, str]:
    """The Host check wants the configured port; port 0 binds another."""
    return {"Host": f"127.0.0.1:{api.cfg.api.port}"}


def test_the_server_starts_on_loopback_and_answers(made):
    server = ApiServer(made.app, 0)
    port = server.start()
    try:
        assert httpx2.get(f"http://127.0.0.1:{port}/api/health", headers=host(made)).json()["ok"] is True
        assert server.server.servers[0].sockets[0].getsockname()[0] == "127.0.0.1"
        assert server.thread.name == "api" and server.thread.daemon
    finally:
        server.stop()
    with pytest.raises(httpx2.ConnectError):
        httpx2.get(f"http://127.0.0.1:{port}/api/health", headers=host(made))


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")  # uvicorn's sys.exit, caught
def test_a_busy_port_is_a_clear_error(made):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with pytest.raises(ConfigError, match=f"Port {port} is in use"):
            ApiServer(made.app, port).start()


def test_the_server_stops_promptly_with_a_stream_open(made):  # Review Focus 2
    server = ApiServer(made.app, 0)
    port = server.start()
    opened, ended = threading.Event(), threading.Event()

    def listen():
        with httpx2.stream("GET", f"http://127.0.0.1:{port}/api/events", timeout=10,
                           headers={**host(made), **bearer(PASSWORD)}) as r:
            opened.set()
            for _ in r.iter_lines():
                pass
        ended.set()

    threading.Thread(target=listen, daemon=True).start()
    assert opened.wait(5)
    made.stop.set()  # Ctrl+C sets the engine's stop
    started = time.monotonic()
    server.stop()
    assert time.monotonic() - started < 3
    assert ended.wait(3) and not server.thread.is_alive()


def test_stop_returns_on_time_even_while_a_request_holds_on(made):
    """A stream that ignores the stop (the engine's isn't set) keeps uvicorn busy for its 2 s grace."""
    server = ApiServer(made.app, 0)
    port = server.start()
    opened = threading.Event()

    def listen():
        try:
            with httpx2.stream("GET", f"http://127.0.0.1:{port}/api/events", timeout=10,
                               headers={**host(made), **bearer(PASSWORD)}) as r:
                opened.set()
                for _ in r.iter_lines():
                    pass
        except httpx2.HTTPError:
            pass  # uvicorn cuts the stream when its grace runs out: the point of this test

    threading.Thread(target=listen, daemon=True).start()
    assert opened.wait(5)
    started = time.monotonic()
    server.stop(timeout=0.3)
    assert time.monotonic() - started < 1.0  # the engine's exit isn't held up
    assert server.server.force_exit and server.thread.is_alive()
    server.thread.join(5)
    assert not server.thread.is_alive()  # uvicorn still ends: it cancels what's left after its grace
