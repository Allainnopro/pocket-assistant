"""`assistant engine`: one long-running process that keeps your opportunities fresh.

| Job           | Every   | Does                                                      |
|---------------|---------|-----------------------------------------------------------|
| sweep         | 1 hour  | deletes email bodies past 7 days (the privacy rule)       |
| check_mail    | 2 min   | asks Gmail for new mail; new mail starts extract at once |
| extract       | 1 min   | runs the queue through Haiku, 4 emails at a time          |
| profile_watch | 30 s    | profile.toml saved: re-ranks everything                   |

The order above is also the catch-up order after the laptop wakes. One engine
at a time (engine.lock); while it runs, the CLI commands that write refuse.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..ingest import query_exclusions
from ..llm import LLM
from ..pipeline import utc_now
from ..store import Store
from ..vault import redact
from .events import EventBus
from .jobs import CheckMailJob, ExtractJob, ProfileHolder, ProfileWatchJob, SweepJob
from .lock import EngineLock, lock_path
from .logs import setup_logging, stop_logging
from .scheduler import Scheduler

SWEEP_EVERY = timedelta(hours=1)
CHECK_EVERY = timedelta(minutes=2)
EXTRACT_EVERY = timedelta(minutes=1)
PROFILE_EVERY = timedelta(seconds=30)
BANNER = "Engine running. New mail is checked every 2 minutes. Press Ctrl+C to stop."


class Engine:
    """Use it from one thread: its Store connection belongs to the thread that built it.
    Setting `stop` (Ctrl+C) lets the running job finish and starts nothing new."""

    def __init__(self, cfg: Config, *, clock: Callable[[], datetime], gmail: Callable[[], Any], llm: LLM,
                 bus: EventBus, log: logging.Logger, stop: threading.Event | None = None,
                 gmail_token: Callable[[], bytes | None] = lambda: None) -> None:
        self.cfg, self.bus, self.log = cfg, bus, log
        self.stop = stop or threading.Event()
        self.store = Store(cfg.db_path)
        try:
            self.holder = ProfileHolder.from_file(cfg.profile_path)
            # Holding engine.lock, no run can be mid-call: give back what a hard stop left claimed
            released = self.store.release_orphaned_claims()
        except BaseException:
            self.store.close()
            raise
        if released:
            log.info(f"gave back {released} attempt(s) held by calls a hard stop cut off")
        unsupported = query_exclusions(cfg.gmail_query)[1]
        if unsupported:
            log.warning(f"config.toml [gmail] query: new mail can't be filtered by {' '.join(unsupported)}; "
                        "only label exclusions (-in:, -category:) apply to it. The rest still applies to "
                        "the catch-up search and `assistant sync`.")
        say = log.info
        self.scheduler = Scheduler(clock, on_error=self._job_failed)
        jobs = [
            ("sweep", SWEEP_EVERY, SweepJob(self.store, say)),
            ("check_mail", CHECK_EVERY, CheckMailJob(self.store, gmail, cfg, bus, self.scheduler, say,
                                                     gmail_token=gmail_token)),
            ("extract", EXTRACT_EVERY, ExtractJob(self.store, llm, self.holder, cfg, bus, say,
                                                  workers=cfg.engine.extract_workers, cancel=self.stop)),
            ("profile_watch", PROFILE_EVERY, ProfileWatchJob(self.store, cfg, self.holder, bus, say)),
        ]
        for name, every, job in jobs:
            self.scheduler.add(name, every, self._clears_its_error(name, job))

    def _clears_its_error(self, name: str, job: Callable[[datetime], timedelta | None]):
        """A job that runs cleanly again ends its "job_error" problem."""
        def run(now: datetime) -> timedelta | None:
            delay = job(now)
            self.bus.report(f"job:{name}", None)
            return delay
        return run

    def _job_failed(self, name: str, exc: BaseException) -> None:
        """A bug or a surprise in one job: log where (never local data), tell the
        dashboard, and let the scheduler run it again at its next time."""
        frames = traceback.extract_tb(exc.__traceback__)
        where = f" (at {Path(frames[-1].filename).name}:{frames[-1].lineno} in {frames[-1].name})" if frames else ""
        self.log.error(f"{name} failed: {type(exc).__name__}: {redact(str(exc))}{where}")
        self.bus.report(f"job:{name}", "job_error", detail=f"{name}: {type(exc).__name__}")

    def run(self, poll_seconds: float = 15.0) -> None:
        self.scheduler.run_forever(self.stop, poll_seconds)

    def close(self) -> None:
        self.store.close()


def wait_until_stopped(thread: threading.Thread, stop: threading.Event, *,
                       sleep: Callable[[float], None] = time.sleep, say: Callable[[str], None] = print) -> None:
    """The main thread waits in short sleeps, because on Windows a blocking wait
    ignores Ctrl+C until it times out. The first Ctrl+C lets the running job
    finish (no paid reply is thrown away); a second one quits at once."""
    while thread.is_alive():
        try:
            sleep(0.2)
        except KeyboardInterrupt:
            if stop.is_set():
                raise
            stop.set()
            say("Stopping after the current job... (Ctrl+C again to quit now)")


def _quit_now() -> None:
    """The second Ctrl+C. Calls on their way are dropped; the attempts their emails
    hold are given back at the next start (Store.release_orphaned_claims)."""
    logging.shutdown()
    os._exit(130)


def run_engine(cfg: Config, dev: bool = False) -> None:
    lock = EngineLock(lock_path(cfg))
    lock.acquire()  # before anything else: a second engine changes nothing
    try:
        log = setup_logging(cfg.db_path.parent / "logs")
        from .. import gmail_imap
        from .. import llm as llm_module
        from .api import ApiContext, create_app
        from .server import ApiServer

        llm = llm_module.VaultLLM()  # no key: say so now, not at the first email
        stop, ready, failed, made = threading.Event(), threading.Event(), [], []

        def engine_thread() -> None:
            try:
                engine = Engine(cfg, clock=utc_now, gmail=lambda: gmail_imap.open_mailbox(cfg), llm=llm,
                                bus=EventBus(), log=log, stop=stop, gmail_token=gmail_imap.token_fingerprint)
            except BaseException as exc:  # a broken profile.toml, a locked database...
                failed.append(exc)
                ready.set()
                return
            made.append(engine)
            ready.set()
            try:
                engine.run()
            except BaseException as exc:
                failed.append(exc)
            finally:
                engine.close()

        thread = threading.Thread(target=engine_thread, name="engine", daemon=True)
        thread.start()
        while not ready.wait(0.1):
            pass
        server = None
        try:
            if not failed:
                password = secrets.token_urlsafe(32)  # new at each launch; the dashboard gets it from Tauri
                ctx = ApiContext(cfg=cfg, holder=made[0].holder, bus=made[0].bus, launch_password=password,
                                 stop=stop, dev=dev)
                server = ApiServer(create_app(ctx), cfg.api.port)
                port = server.start()
                log.info(f"engine started ({cfg.engine.extract_workers} extract workers)")
                print(BANNER)
                print(f"API on http://127.0.0.1:{port}")
                if dev:
                    print(f"Launch password: {password}")  # printed only, never logged
                try:
                    wait_until_stopped(thread, stop)
                except KeyboardInterrupt:
                    stop.set()  # whatever is still queued must not start
                    print("Stopped without waiting for the current job.")
                    _quit_now()
        finally:
            try:
                if server is not None:
                    server.stop()  # the server first: it holds no job, and its streams end on `stop`
                if thread.is_alive():  # a failed start: the engine goes too, after its current job
                    stop.set()
                    while thread.is_alive():
                        thread.join(0.2)
            except KeyboardInterrupt:  # the second Ctrl+C can land here too
                stop.set()
                print("Stopped without waiting for the current job.")
                _quit_now()
        if failed:
            raise failed[0]
        log.info("engine stopped")
        print("Engine stopped.")
    finally:
        stop_logging()
        lock.release()
