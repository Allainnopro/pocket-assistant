"""`assistant engine`: one long-running process that keeps your opportunities fresh.

| Job           | Every   | Does                                                      |
|---------------|---------|-----------------------------------------------------------|
| sweep         | 1 hour  | deletes email bodies past 7 days (the privacy rule)       |
| check_mail    | 2 min   | reads Gmail's change feed; new mail starts extract at once |
| extract       | 1 min   | runs the queue through Haiku, 4 emails at a time          |
| profile_watch | 30 s    | profile.toml saved: re-ranks everything                   |

The order above is also the catch-up order after the laptop wakes. One engine
at a time (engine.lock); while it runs, the CLI commands that write refuse.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..llm import LLM
from ..pipeline import utc_now
from ..store import Store
from ..vault import redact
from .events import STATUS, EventBus
from .jobs import CheckMailJob, ExtractJob, ProfileHolder, ProfileWatchJob, SweepJob
from .lock import EngineLock, lock_path
from .logs import setup_logging
from .scheduler import Scheduler

SWEEP_EVERY = timedelta(hours=1)
CHECK_EVERY = timedelta(minutes=2)
EXTRACT_EVERY = timedelta(minutes=1)
PROFILE_EVERY = timedelta(seconds=30)
BANNER = "Engine running. New mail is checked every 2 minutes. Press Ctrl+C to stop."


class Engine:
    """Use it from one thread: its Store connection belongs to the thread that built it."""

    def __init__(self, cfg: Config, *, clock: Callable[[], datetime], gmail: Callable[[], Any], llm: LLM,
                 bus: EventBus, log: logging.Logger) -> None:
        self.cfg, self.bus, self.log = cfg, bus, log
        self.store = Store(cfg.db_path)
        try:
            self.holder = ProfileHolder.from_file(cfg.profile_path)
        except BaseException:
            self.store.close()
            raise
        say = log.info
        self.scheduler = Scheduler(clock, on_error=self._job_failed)
        self.scheduler.add("sweep", SWEEP_EVERY, SweepJob(self.store, say))
        self.scheduler.add("check_mail", CHECK_EVERY, CheckMailJob(self.store, gmail, cfg, bus, self.scheduler, say))
        self.scheduler.add("extract", EXTRACT_EVERY, ExtractJob(self.store, llm, self.holder, cfg, bus, say,
                                                                workers=cfg.engine.extract_workers))
        self.scheduler.add("profile_watch", PROFILE_EVERY, ProfileWatchJob(self.store, cfg, self.holder, bus, say))

    def _job_failed(self, name: str, exc: BaseException) -> None:
        """A bug or a surprise in one job: log where (never local data), tell the
        dashboard, and let the scheduler run it again at its next time."""
        frames = traceback.extract_tb(exc.__traceback__)
        where = f" (at {Path(frames[-1].filename).name}:{frames[-1].lineno} in {frames[-1].name})" if frames else ""
        self.log.error(f"{name} failed: {type(exc).__name__}: {redact(str(exc))}{where}")
        self.bus.publish(STATUS, {"problem": "job_error", "detail": f"{name}: {type(exc).__name__}"})

    def run(self, stop: threading.Event, poll_seconds: float = 15.0) -> None:
        self.scheduler.run_forever(stop, poll_seconds)

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


def run_engine(cfg: Config) -> None:
    lock = EngineLock(lock_path(cfg))
    lock.acquire()  # before anything else: a second engine changes nothing
    try:
        log = setup_logging(cfg.db_path.parent / "logs")
        from ..gmail_client import build_service, get_credentials
        from ..llm import AnthropicLLM

        llm = AnthropicLLM.from_vault()  # no key: say so now, not at the first email
        stop, ready, failed = threading.Event(), threading.Event(), []

        def engine_thread() -> None:
            try:
                engine = Engine(cfg, clock=utc_now, gmail=lambda: build_service(get_credentials()), llm=llm,
                                bus=EventBus(), log=log)
            except BaseException as exc:  # a broken profile.toml, a locked database...
                failed.append(exc)
                ready.set()
                return
            ready.set()
            try:
                engine.run(stop)
            except BaseException as exc:
                failed.append(exc)
            finally:
                engine.close()

        thread = threading.Thread(target=engine_thread, name="engine", daemon=True)
        thread.start()
        while not ready.wait(0.1):
            pass
        if not failed:
            log.info(f"engine started ({cfg.engine.extract_workers} extract workers)")
            print(BANNER)
            try:
                wait_until_stopped(thread, stop)
            except KeyboardInterrupt:
                print("Stopped without waiting for the current job.")
                return
        if failed:
            raise failed[0]
        log.info("engine stopped")
        print("Engine stopped.")
    finally:
        lock.release()
