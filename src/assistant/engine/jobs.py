"""The engine's four jobs. Each is a callable matching scheduler.JobFn: it gets the
time it started and may return a delay that replaces its interval once.

Failure handling, explained:

| What happened                          | What the job does                                  |
|----------------------------------------|----------------------------------------------------|
| Gmail unreachable (network, Google 5xx)| "offline"; retry in 5 min, then every 15 min       |
| Gmail access lost (token revoked/expired) | "auth_lost"; retry in 15 min (`assistant auth`) |
| Daily LLM cap reached                  | wait until midnight in Ho Chi Minh City            |
| Anthropic busy or no answer            | retry in 5 min                                     |
| Anthropic rejected our request or key  | "anthropic"; retry in 60 min                       |
| profile.toml saved broken              | "profile"; keep the last good profile              |
| Anything else                          | the scheduler logs it and runs the job as usual    |

Problems go out as STATUS events; the dashboard (M3) and Telegram (M6) show them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..costs import TIMEZONE
from ..errors import ConfigError, GmailAuthError
from ..llm import LLM
from ..pipeline import run_extraction, utc_now
from ..profile import Profile, load_profile
from ..rank import score
from ..store import BODY_RETENTION_DAYS, Store
from ..vault import redact
from .events import ITEMS_NEW, RANKING_CHANGED, STATUS, EventBus
from .gmail_feed import check_mail
from .scheduler import Scheduler

AUTH_RETRY = timedelta(minutes=15)
OFFLINE_FIRST, OFFLINE_LATER = timedelta(minutes=5), timedelta(minutes=15)
BUSY_RETRY = timedelta(minutes=5)
FATAL_RETRY = timedelta(minutes=60)

Log = Callable[[str], None]


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class ProfileHolder:
    """The profile every job reads, swapped whole when profile.toml changes."""

    def __init__(self, profile: Profile, mtime_ns: int | None) -> None:
        self._lock = threading.Lock()
        self._profile, self._mtime = profile, mtime_ns

    @classmethod
    def from_file(cls, path: Path) -> ProfileHolder:
        mtime = _mtime(path)  # before reading: an edit during the read shows up as a change
        return cls(load_profile(path), mtime)

    def get(self) -> Profile:
        with self._lock:
            return self._profile

    @property
    def mtime_ns(self) -> int | None:
        with self._lock:
            return self._mtime

    def set(self, profile: Profile, mtime_ns: int | None) -> None:
        with self._lock:
            self._profile, self._mtime = profile, mtime_ns


def _offline(exc: BaseException) -> bool:
    """The network or Google is down: nothing for the owner to fix."""
    import httplib2
    from google.auth.exceptions import TransportError
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        return exc.resp.status >= 500
    return isinstance(exc, (OSError, httplib2.HttpLib2Error, TransportError))


class CheckMailJob:
    def __init__(self, store: Store, gmail: Callable[[], Any], cfg: Config, bus: EventBus,
                 scheduler: Scheduler, log: Log = print) -> None:
        self.store, self.gmail, self.cfg, self.bus, self.scheduler, self.log = store, gmail, cfg, bus, scheduler, log
        self._service = None  # built on first use, dropped after any failure
        self._failures = 0    # offline checks in a row

    def __call__(self, now: datetime) -> timedelta | None:
        from google.auth.exceptions import RefreshError

        try:
            if self._service is None:
                self._service = self.gmail()
            result = check_mail(self.store, self._service, self.cfg, now_utc=now, log=self.log)
        except (GmailAuthError, RefreshError):
            self._service = None
            self.log("check_mail: Gmail access lost; run `assistant auth`")
            return self._problem("auth_lost", online=True, retry=AUTH_RETRY)
        except Exception as exc:
            self._service = None
            if not _offline(exc):
                raise
            self._failures += 1
            self.log(f"check_mail: offline ({type(exc).__name__})")
            return self._problem("offline", online=False,
                                 retry=OFFLINE_FIRST if self._failures == 1 else OFFLINE_LATER)
        self._failures = 0
        self.bus.publish(STATUS, {"online": True, "last_ok_check_at": self.store.get_state("last_ok_check_at"),
                                  "problem": None})
        if result.new:
            self.log(f"check_mail: {result.new} new ({result.mode})")
            self.scheduler.run_soon("extract")
        return None

    def _problem(self, problem: str, *, online: bool, retry: timedelta) -> timedelta:
        self.store.set_state("last_check_error", problem)
        self.bus.publish(STATUS, {"online": online, "last_ok_check_at": self.store.get_state("last_ok_check_at"),
                                  "problem": problem})
        return retry


def until_midnight(now: datetime) -> timedelta:
    """Until the next 00:00 in Ho Chi Minh City, when the daily cap resets."""
    local = now.astimezone(TIMEZONE)
    return datetime.combine(local.date() + timedelta(days=1), time(), tzinfo=TIMEZONE) - now


class ExtractJob:
    def __init__(self, store: Store, llm: LLM, holder: ProfileHolder, cfg: Config, bus: EventBus,
                 log: Log = print, workers: int = 1, clock: Callable[[], datetime] = utc_now) -> None:
        self.store, self.llm, self.holder, self.cfg, self.bus, self.log = store, llm, holder, cfg, bus, log
        self.workers, self.clock = workers, clock

    def __call__(self, now: datetime) -> timedelta | None:
        if not self.store.queue():
            return None
        report = run_extraction(self.store, self.llm, self.holder.get(), model=self.cfg.llm.extract_model,
                                caps=self.cfg.llm.caps, clock=self.clock, log=self.log, workers=self.workers)
        new, merged = report.counts["new"], report.counts["merged"]
        if new + merged:
            self.bus.publish(ITEMS_NEW, {"new": new, "merged": merged})
        if report.stopped:
            self.log(f"extract: stopped early: {report.stopped}")
        if report.stop_kind == "cap_daily":
            return until_midnight(now)
        if report.stop_kind in ("busy", "no_answer"):
            return BUSY_RETRY
        if report.stop_kind == "fatal":
            self.bus.publish(STATUS, {"problem": "anthropic", "detail": redact(str(report.fatal))})
            return FATAL_RETRY
        return None


class SweepJob:
    """The privacy rule: email bodies are deleted after 7 days, extracted or not."""

    def __init__(self, store: Store, log: Log = print) -> None:
        self.store, self.log = store, log

    def __call__(self, now: datetime) -> None:
        deleted, unextracted = self.store.purge_stale_bodies()
        if deleted:
            self.log(f"sweep: deleted {deleted} email bodies older than {BODY_RETENTION_DAYS} days "
                     f"({unextracted} never extracted)")


class ProfileWatchJob:
    """Edit profile.toml while the engine runs: the rankings follow within 30 s."""

    def __init__(self, store: Store, cfg: Config, holder: ProfileHolder, bus: EventBus, log: Log = print) -> None:
        self.store, self.cfg, self.holder, self.bus, self.log = store, cfg, holder, bus, log
        self._seen = holder.mtime_ns

    def __call__(self, now: datetime) -> None:
        mtime = _mtime(self.cfg.profile_path)
        if mtime == self._seen:
            return
        try:
            profile = load_profile(self.cfg.profile_path)
        except ConfigError as exc:  # report it once; keep ranking with the last good profile
            self._seen = mtime
            self.log(f"profile_watch: {exc} (keeping the previous profile)")
            self.bus.publish(STATUS, {"problem": "profile", "detail": str(exc)})
            return
        self._seen = mtime  # only now: a read that failed some other way is retried next time
        self.holder.set(profile, mtime)
        today = now.astimezone(TIMEZONE).date().isoformat()
        rescored, expired = self.store.rescore_opportunities(lambda o: score(o, profile)[0], today)
        self.log(f"profile_watch: profile.toml changed; rescored {rescored}, {expired} newly expired")
        self.bus.publish(RANKING_CHANGED, {"rescored": rescored})
