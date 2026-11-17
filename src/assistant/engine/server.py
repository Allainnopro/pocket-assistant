"""Uvicorn in a thread of its own, so the engine's main thread stays free for Ctrl+C.

Uvicorn installs no signal handlers off the main thread. Stopping it waits for open
requests at most 2 s (timeout_graceful_shutdown); event streams end by themselves
once the engine's stop is set.
"""

from __future__ import annotations

import threading
import time

import uvicorn

from ..errors import ConfigError


class ApiServer:
    def __init__(self, app, port: int) -> None:
        self.port = port
        # access_log off: request URLs carry search terms, and logs hold no local data
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                                    access_log=False, timeout_graceful_shutdown=2))
        self.thread = threading.Thread(target=self._run, name="api", daemon=True)

    def _run(self) -> None:
        try:
            self.server.run()
        except SystemExit:
            pass  # uvicorn's way of saying it couldn't bind; start() turns that into a clear error

    def start(self, timeout: float = 10.0) -> int:
        """The port it listens on. Uvicorn exits its thread when it can't bind."""
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop(timeout=1.0)
                raise ConfigError(f"Port {self.port} is in use (set [api] port in config.toml)")
            time.sleep(0.02)
        return self.server.servers[0].sockets[0].getsockname()[1]

    def stop(self, timeout: float = 5.0) -> None:
        """Joins in short slices: on Windows a long join ignores Ctrl+C."""
        self.server.should_exit = True
        deadline = time.monotonic() + timeout
        while self.thread.is_alive() and time.monotonic() < deadline:
            self.thread.join(0.1)
        if self.thread.is_alive():
            self.server.force_exit = True
