"""The engine's four jobs. Each is a callable matching scheduler.JobFn: it gets the
time it started and may return a delay that replaces its interval once.

Failure handling, explained:

| What happened                          | What the job does                                  |
|----------------------------------------|----------------------------------------------------|
| Gmail unreachable (network, cut off)   | retry in 30 s (Wi-Fi reconnecting after a wake),   |
|                                        | then "offline": 5 min, then every 15 min           |
| Gmail limiting this account            | "gmail_busy"; retry in 5 min, then every 15 min    |
| Gmail refused the password, or none set| "auth_lost"; a new `assistant set-key gmail` is    |
|                                        | used within a minute; the same one is tried hourly,|
|                                        | so repeated failures can't get the account locked  |
| Daily LLM cap reached                  | wait until midnight in Ho Chi Minh City            |
| Anthropic busy or no answer            | retry in 5 min, then 15, then every 60 min         |
| No network (nothing was sent)          | the same, but over within a minute of a mail check |
|                                        | getting through                                    |
| Anthropic rejected our request or key  | "anthropic"; retry in 60 min, or within a minute   |
|                                        | of `assistant set-key` saving a different key      |
| profile.toml saved broken              | "profile"; keep the last good profile              |
|                                        | (an empty file is an editor mid-save: look again)  |

New mail asks for an extract run at once (run_soon), but never cuts these waits short.
| Anything else                          | the scheduler logs it and runs the job as usual    |

Problems are kept per source on the EventBus and go out as STATUS events; the
dashboard (M3) and Telegram (M6) show them.
"""

from __future__ import annotations

import imaplib
import threading
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..costs import TIMEZONE
from ..errors import ConfigError, GmailAuthError
from ..gmail_imap import GmailBusy
from ..llm import LLM
from ..pipeline import run_extraction, utc_now
from ..profile import Profile, load_profile
from ..rank import score
from ..store import BODY_RETENTION_DAYS, Store
from ..vault import redact
from .events import ITEMS_NEW, RANKING_CHANGED, EventBus
from .gmail_feed import check_mail
from .scheduler import Scheduler

AUTH_RETRY = timedelta(minutes=60)   # the same refused password, tried again
RECHECK = timedelta(minutes=1)       # free local checks: a new password, a new key, the network back
OFFLINE_RETRIES = (timedelta(seconds=30), timedelta(minutes=5), timedelta(minutes=15))
GMAIL_BUSY_RETRIES = (timedelta(minutes=5), timedelta(minutes=15))
BUSY_RETRIES = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))  # in a row
FATAL_RETRY = timedelta(minutes=60)

Log = Callable[[str], None]


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None  # missing: load_profile says so


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


def _gmail_trouble(exc: BaseException) -> str | None:
    """"auth_lost" (the owner must save a new password), "offline" or "gmail_busy" (wait
    it out), or None: an error nobody planned for, which goes to the scheduler."""
    if isinstance(exc, GmailAuthError):
        return "auth_lost"
    if isinstance(exc, GmailBusy):
        return "gmail_busy"
    if isinstance(exc, PermissionError):  # an OSError, but this is the read-only gate tripping
        return None
    if isinstance(exc, (OSError, imaplib.IMAP4.abort)):
        return "offline"
    return None


class CheckMailJob:
    def __init__(self, store: Store, gmail: Callable[[], Any], cfg: Config, bus: EventBus,
                 scheduler: Scheduler, log: Log = print,
                 gmail_token: Callable[[], bytes | None] = lambda: None) -> None:
        """gmail: opens a Mailbox (gmail_imap.open_mailbox), a fresh one for each check.
        gmail_token: a fingerprint of the saved password (gmail_imap.token_fingerprint)."""
        self.store, self.gmail, self.cfg, self.bus, self.scheduler, self.log = store, gmail, cfg, bus, scheduler, log
        self.gmail_token = gmail_token
        self._auth_lost_at: datetime | None = None
        self._lost_with: bytes | None = None  # the saved password that was refused
        self._failures = 0    # failed checks in a row (offline or rate limited)

    def __call__(self, now: datetime) -> timedelta | None:
        saved = self.gmail_token()
        if self._auth_lost_at is not None:
            if saved == self._lost_with and now < self._auth_lost_at + AUTH_RETRY:
                return RECHECK  # the same refused password: wait for `assistant set-key gmail`
            self._auth_lost_at = None
        try:
            with self.gmail() as mailbox:
                result = check_mail(self.store, mailbox, self.cfg, now_utc=now, log=self.log)
        except Exception as exc:
            trouble = _gmail_trouble(exc)
            if trouble is None:
                raise
            if trouble == "auth_lost":
                self._auth_lost_at, self._lost_with = now, saved
                self.log(f"check_mail: Gmail access lost. {exc}")  # our own wording, never the server's
                return self._problem("auth_lost", online=True, retry=RECHECK, detail=str(exc))
            self._failures += 1
            if trouble == "gmail_busy":
                self.log("check_mail: Gmail is rate limiting; backing off")
                return self._problem("gmail_busy", online=True,
                                     retry=GMAIL_BUSY_RETRIES[min(self._failures, 2) - 1])
            if self._failures == 1:  # no "offline" yet: after a wake, Wi-Fi is often still connecting
                self.log(f"check_mail: no connection ({type(exc).__name__}); trying again in 30 s")
                return OFFLINE_RETRIES[0]
            self.log(f"check_mail: offline ({type(exc).__name__})")
            return self._problem("offline", online=False, retry=OFFLINE_RETRIES[min(self._failures, 3) - 1])
        self._failures = 0
        self.bus.report("gmail", None, online=True, last_ok_check_at=self.store.get_state("last_ok_check_at"))
        if result.new:
            self.log(f"check_mail: {result.new} new ({result.mode})")
            self.scheduler.run_soon("extract")
        return None

    def _problem(self, problem: str, *, online: bool, retry: timedelta, **details: Any) -> timedelta:
        self.store.set_state("last_check_error", problem)
        self.bus.report("gmail", problem, online=online, last_ok_check_at=self.store.get_state("last_ok_check_at"),
                        **details)
        return retry


def until_midnight(now: datetime) -> timedelta:
    """Until the next 00:00 in Ho Chi Minh City, when the daily cap resets."""
    local = now.astimezone(TIMEZONE)
    return datetime.combine(local.date() + timedelta(days=1), time(), tzinfo=TIMEZONE) - now


class ExtractJob:
    """llm may offer refresh() -> bool (llm.VaultLLM): during a wait after a rejected
    key, a newly saved key ends the wait."""

    def __init__(self, store: Store, llm: LLM, holder: ProfileHolder, cfg: Config, bus: EventBus,
                 log: Log = print, workers: int = 1, clock: Callable[[], datetime] = utc_now,
                 cancel: threading.Event | None = None) -> None:
        self.store, self.llm, self.holder, self.cfg, self.bus, self.log = store, llm, holder, cfg, bus, log
        self.workers, self.clock, self.cancel = workers, clock, cancel
        self._wait: tuple[datetime, datetime] | None = None  # (from, until): kept even when new mail asks
        self._wait_for = ""  # "busy", "offline", "cap" or "key"
        self._busy_in_a_row = 0

    def __call__(self, now: datetime) -> timedelta | None:
        if self._wait is not None:
            since, until = self._wait
            if now < since:  # the clock was set back: the wait no longer means anything
                self._end_wait()
            elif self._wait_for == "key" and self._new_key():
                self.log("extract: a new API key was saved; trying again")
                self._end_wait()
            elif self._wait_for == "offline" and self._mail_check_since(since):
                self.log("extract: the connection is back; trying again")
                self._end_wait()
            elif now < until:
                # A new key or the network coming back costs nothing to notice: look every minute
                return min(until - now, RECHECK) if self._wait_for in ("key", "offline") else until - now
            else:
                self._end_wait()
        if not self.store.queue():
            return None
        try:
            report = run_extraction(self.store, self.llm, self.holder.get(), model=self.cfg.llm.extract_model,
                                    caps=self.cfg.llm.caps, clock=self.clock, log=self.log, workers=self.workers,
                                    cancel=self.cancel, show_subjects=False)
        except Exception as exc:  # our own bug: still announce what was saved, still keep a wait
            if getattr(exc, "report", None) is not None:
                self._after(exc.report, now)
            raise
        return self._after(report, now)

    def _after(self, report, now: datetime) -> timedelta | None:
        new, merged = report.counts["new"], report.counts["merged"]
        if new + merged:
            self.bus.publish(ITEMS_NEW, {"new": new, "merged": merged})
        if report.stopped:
            why = f" ({redact(str(report.fatal))})" if report.fatal else ""
            self.log(f"extract: stopped early: {report.stopped}{why}")
        if report.answered and report.stop_kind != "fatal":
            self.bus.report("anthropic", None)  # an answer: the key and the account work
        if report.stop_kind in ("busy", "no_answer", "offline"):
            self._busy_in_a_row += 1
            delay = BUSY_RETRIES[min(self._busy_in_a_row, len(BUSY_RETRIES)) - 1]
            return self._start_wait(now, delay, "offline" if report.stop_kind == "offline" else "busy")
        self._busy_in_a_row = 0
        if report.stop_kind == "cap_daily":
            return self._start_wait(now, until_midnight(now), "cap")
        if report.stop_kind == "fatal":
            self.bus.report("anthropic", "anthropic", detail=redact(str(report.fatal)))
            return self._start_wait(now, FATAL_RETRY, "key")
        return None

    def _start_wait(self, now: datetime, delay: timedelta, wait_for: str) -> timedelta:
        """Returns when the scheduler should call again: at the end of the wait, or in a
        minute for a wait a new key or the network's return can end early."""
        self._wait, self._wait_for = (now, now + delay), wait_for
        return min(delay, RECHECK) if wait_for in ("key", "offline") else delay

    def _end_wait(self) -> None:
        self._wait, self._wait_for = None, ""

    def _mail_check_since(self, since: datetime) -> bool:
        """A Gmail check got through after the wait began: the network is back."""
        ok = self.store.get_state("last_ok_check_at")
        return ok is not None and datetime.fromisoformat(ok) > since

    def _new_key(self) -> bool:
        refresh = getattr(self.llm, "refresh", None)
        return bool(refresh and refresh())


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
        if _size(self.cfg.profile_path) == 0:  # an editor that truncates, then writes: look again next time
            return
        try:
            profile = load_profile(self.cfg.profile_path)
        except ConfigError as exc:  # report it once; keep ranking with the last good profile
            self._seen = mtime
            self.log(f"profile_watch: {exc} (keeping the previous profile)")
            self.bus.report("profile", "profile", detail=str(exc))
            return
        self._seen = mtime  # only now: a read that failed some other way is retried next time
        self.holder.set(profile, mtime)
        self.bus.report("profile", None)
        today = now.astimezone(TIMEZONE).date().isoformat()
        rescored, expired = self.store.rescore_opportunities(lambda o: score(o, profile)[0], today)
        self.log(f"profile_watch: profile.toml changed; rescored {rescored}, {expired} newly expired")
        self.bus.publish(RANKING_CHANGED, {"rescored": rescored})
