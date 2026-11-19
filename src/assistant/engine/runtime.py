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
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import ConfigError
from ..ingest import query_exclusions
from ..llm import LLM
from ..pipeline import utc_now
from ..store import Store
from ..vault import redact
from .events import EventBus
from .jobs import CheckMailJob, ExtractJob, ProfileHolder, ProfileWatchJob, SweepJob
from .lock import EngineLock, lock_path
from .parent import parent_alive
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
            # new kind rules (email_kinds.RULES_VERSION) reach the mail already stored; the AI's answers stay
            resorted = (self.store.resort_kinds((cfg.gmail_address, *cfg.university_addresses))
                        if self.store.kinds_outdated() else 0)
        except BaseException:
            self.store.close()
            raise
        if released:
            log.info(f"gave back {released} attempt(s) held by calls a hard stop cut off")
        if resorted:
            log.info(f"sorted {resorted} stored email(s) with the current kind rules")
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


# --dev only: the browser dashboard's dev server (app/vite.config.ts) reads the launch password here and
# hands it to its own page, so the owner never copies it out of a terminal (where Ctrl+C means "stop").
# Only a checkout has app/; the file lives while the engine runs, and a stale one is useless (new each start).
DEV_PASSWORD_FILE = Path(__file__).resolve().parents[3] / "app" / ".launch-password"


def _hand_over(password: str) -> None:
    if DEV_PASSWORD_FILE.parent.is_dir():
        DEV_PASSWORD_FILE.write_text(password, encoding="utf-8")


def _quit_now() -> None:
    """The second Ctrl+C. Calls on their way are dropped; the attempts their emails
    hold are given back at the next start (Store.release_orphaned_claims)."""
    logging.shutdown()
    os._exit(130)


READY_LINE = "READY port={port}"  # the desktop app waits for this line (the start-up handshake)
PARENT_CHECK_SECONDS = 5.0


def _stop_on_end_of_stdin(stdin, stop: threading.Event) -> None:
    """The desktop app's Quit closes our stdin: stop as after one Ctrl+C (the current job finishes)."""
    try:
        while stdin.readline():
            pass
    except (OSError, ValueError):
        pass  # the pipe broke: the app is gone, which is a stop too
    stop.set()


def _stop_when_parent_is_gone(pid: int, stop: threading.Event) -> None:
    """The app crashed or was killed without closing our stdin: stop within PARENT_CHECK_SECONDS."""
    while not stop.wait(PARENT_CHECK_SECONDS):
        if not parent_alive(pid):
            stop.set()


def run_engine(cfg: Config, dev: bool = False, parent_pid: int | None = None, password_stdin: bool = False,
               stdin=None) -> None:
    """dev: print the launch password and hand it to the Vite dev server. password_stdin: the desktop app writes
    the launch password as stdin's first line and closes stdin to stop us; parent_pid: the app's process."""
    lock = EngineLock(lock_path(cfg))
    lock.acquire()  # before anything else: a second engine changes nothing
    if password_stdin:
        stdin = stdin if stdin is not None else sys.stdin
        given = stdin.readline().strip()  # never printed, never logged
        if not given:
            lock.release()
            raise ConfigError("--password-stdin: no launch password arrived on stdin")
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
                # new at each launch: from the desktop app (stdin), or our own for a terminal start
                password = given if password_stdin else secrets.token_urlsafe(32)
                ctx = ApiContext(cfg=cfg, holder=made[0].holder, bus=made[0].bus, launch_password=password,
                                 stop=stop, dev=dev)
                server = ApiServer(create_app(ctx), cfg.api.port)
                port = server.start()
                log.info(f"engine started ({cfg.engine.extract_workers} extract workers)")
                print(BANNER)
                print(f"API on http://127.0.0.1:{port}")
                if dev:
                    print(f"Launch password: {password}")  # printed only, never logged
                    _hand_over(password)
                if password_stdin:
                    threading.Thread(target=_stop_on_end_of_stdin, args=(stdin, stop), name="stdin-watch",
                                     daemon=True).start()
                if parent_pid is not None:
                    threading.Thread(target=_stop_when_parent_is_gone, args=(parent_pid, stop), name="parent-watch",
                                     daemon=True).start()
                print(READY_LINE.format(port=port), flush=True)
                try:
                    wait_until_stopped(thread, stop)
                except KeyboardInterrupt:
                    stop.set()  # whatever is still queued must not start
                    print("Stopped without waiting for the current job.")
                    _quit_now()
        finally:
            if dev:
                DEV_PASSWORD_FILE.unlink(missing_ok=True)
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
