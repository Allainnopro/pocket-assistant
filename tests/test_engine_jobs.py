"""The engine's jobs and its event bus.

Run: pytest
"""

import os
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fake_gmail import FakeGmail
from fake_llm import SameReplyLLM
from test_parser import make_message
from test_pipeline import add_mail, item, mail, outcomes, reply
from test_rank import opp
from test_scheduler import FakeClock

from assistant.config import load_config
from assistant.costs import Caps
from assistant.engine.events import ITEMS_NEW, RANKING_CHANGED, STATUS, EventBus
from assistant.engine.jobs import CheckMailJob, ExtractJob, ProfileHolder, ProfileWatchJob, SweepJob
from assistant.engine.scheduler import Scheduler
from assistant.errors import GmailAuthError
from assistant.llm import LlmNoAnswer, LlmRejected, LlmUnreachable
from assistant.rank import score
from assistant.store import Store

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "profile.example.toml", tmp_path / "profile.toml")
    return load_config(tmp_path / "config.toml")


@pytest.fixture
def store(cfg):
    store = Store(cfg.db_path)
    yield store
    store.close()


def drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


# --- the event bus -------------------------------------------------------------------------------

def test_every_subscriber_gets_each_event():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish(ITEMS_NEW, {"new": 2, "merged": 1})
    assert drain(a) == drain(b) == [{"kind": "items_new", "new": 2, "merged": 1}]
    bus.unsubscribe(b)
    bus.publish(STATUS, {"online": True})
    assert len(drain(a)) == 1 and drain(b) == []


def test_a_slow_subscriber_drops_its_own_oldest_event_only():
    bus = EventBus()
    slow, fast = bus.subscribe(max_queue=2), bus.subscribe()
    for n in range(3):
        bus.publish(ITEMS_NEW, {"new": n, "merged": 0})
    assert [e["new"] for e in drain(slow)] == [1, 2]
    assert [e["new"] for e in drain(fast)] == [0, 1, 2]


# --- checking mail -------------------------------------------------------------------------------

def check_job(store, cfg, gmail, bus, sched=None):
    sched = sched or Scheduler(FakeClock(NOW))
    sched.add("extract", MIN, lambda now: None)
    return CheckMailJob(store, gmail, cfg, bus, sched, log=lambda line: None), sched


def new_mail_gmail():
    fake = FakeGmail([make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id="a")],
                     profile="me@x.com", history_id="200")
    return fake


def test_new_mail_makes_extraction_run_at_once(store, cfg):
    bus = EventBus()
    events = bus.subscribe()
    job, sched = check_job(store, cfg, lambda: new_mail_gmail(), bus)
    assert sched.tick() == ["extract"]  # its first run, at start-up
    assert job(NOW) is None
    assert sched.tick() == ["extract"]  # due in a minute, but new mail brings it forward
    assert drain(events) == [{"kind": "status", "source": "gmail", "problem": None, "online": True,
                              "last_ok_check_at": NOW.isoformat()}]


def test_no_new_mail_leaves_extraction_on_its_schedule(store, cfg):
    job, sched = check_job(store, cfg, lambda: FakeGmail([], profile="me@x.com"), EventBus())
    sched.tick()
    job(NOW)
    assert sched.tick() == []


class FlakyGmail(FakeGmail):
    """Loses the network in the middle of a check."""

    def users(self):
        raise OSError("connection reset")


def test_offline_retries_in_30_seconds_then_backs_off_5_then_15_minutes_then_recovers(store, cfg):
    built = []
    services = iter([OSError("no route to host"), FlakyGmail([], profile="me@x.com"), OSError("still down"),
                     new_mail_gmail()])

    def gmail():
        built.append(1)
        service = next(services)
        if isinstance(service, BaseException):
            raise service
        return service

    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, gmail, bus)
    assert job(NOW) == timedelta(seconds=30)  # after a wake, Wi-Fi is often still connecting
    assert drain(events) == [] and store.get_state("last_check_error") is None  # not "offline" yet
    assert job(NOW) == 5 * MIN  # a service that failed mid-check is dropped and built again
    assert store.get_state("last_check_error") == "offline"
    assert job(NOW) == 15 * MIN
    assert job(NOW) is None
    assert job(NOW) is None and len(built) == 4  # a working service is kept
    problems = [(e["source"], e["problem"]) for e in drain(events)]
    assert problems == [("gmail", "offline"), ("gmail", "offline"), ("gmail", None), ("gmail", None)]
    assert store.get_state("last_check_error") is None


def test_lost_gmail_access_is_reported_and_tried_again_after_15_minutes(store, cfg):
    tries = []

    def gmail():
        tries.append(1)
        raise GmailAuthError("Token refresh failed. Run: assistant auth")

    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, gmail, bus)
    assert job(NOW) == MIN  # looks for a new sign-in every minute...
    assert job(NOW + 5 * MIN) == MIN and len(tries) == 1  # ...without retrying the dead one
    assert job(NOW + 15 * MIN) == MIN and len(tries) == 2  # which it tries again after 15 minutes
    assert [(e["source"], e["problem"]) for e in drain(events)] == [("gmail", "auth_lost")] * 2
    assert store.get_state("last_check_error") == "auth_lost"


def test_a_google_sign_in_hiccup_is_offline_not_lost_access(store, cfg):
    from google.auth.exceptions import RefreshError

    def gmail():
        try:  # as gmail_client.get_credentials wraps it
            raise RefreshError("temporarily_unavailable", retryable=True)
        except RefreshError as exc:
            raise GmailAuthError("Token refresh failed. Run: assistant auth") from exc

    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, gmail, bus)
    assert job(NOW) == timedelta(seconds=30) and job(NOW) == 5 * MIN
    assert [e["problem"] for e in drain(events)] == ["offline"]
    assert store.get_state("last_check_error") == "offline"


def http_error(status, reason=None):
    import json

    import httplib2
    from googleapiclient.errors import HttpError

    error = {"code": status, "message": "error", "errors": [{"reason": reason}] if reason else []}
    return HttpError(httplib2.Response({"status": status}), json.dumps({"error": error}).encode())


class FailingGmail(FakeGmail):
    def __init__(self, error):
        super().__init__([], profile="me@x.com")
        self.error = error

    def users(self):
        raise self.error


@pytest.mark.parametrize("error", [http_error(429), http_error(403, "userRateLimitExceeded"),
                                   http_error(403, "rateLimitExceeded")])
def test_a_gmail_rate_limit_backs_off_5_then_15_minutes(store, cfg, error):
    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, lambda: FailingGmail(error), bus)
    assert job(NOW) == 5 * MIN and job(NOW) == 15 * MIN
    assert [(e["source"], e["problem"]) for e in drain(events)] == [("gmail", "gmail_busy")] * 2


def test_a_dropped_http_connection_counts_as_offline(store, cfg):
    import http.client

    job, _ = check_job(store, cfg, lambda: FailingGmail(http.client.IncompleteRead(b"")), EventBus())
    assert job(NOW) == timedelta(seconds=30)


def test_a_refused_gmail_request_still_goes_to_the_scheduler(store, cfg):
    from googleapiclient.errors import HttpError

    job, _ = check_job(store, cfg, lambda: FailingGmail(http_error(403, "insufficientPermissions")), EventBus())
    with pytest.raises(HttpError):
        job(NOW)


def test_each_source_keeps_its_own_problem():
    bus = EventBus()
    events = bus.subscribe()
    bus.report("anthropic", "anthropic", detail="key rejected")
    bus.report("gmail", None, online=True, last_ok_check_at="t")
    assert bus.problems() == {"anthropic": {"problem": "anthropic", "detail": "key rejected"}}
    bus.report("profile", None)  # nothing to clear: nothing to say
    bus.report("anthropic", None)
    assert bus.problems() == {}
    assert [(e["source"], e["problem"]) for e in drain(events)] == [
        ("anthropic", "anthropic"), ("gmail", None), ("anthropic", None)]


def test_a_good_mail_check_does_not_hide_other_problems(store, cfg):
    bus = EventBus()
    bus.report("anthropic", "anthropic", detail="key rejected")
    job, _ = check_job(store, cfg, lambda: FakeGmail([], profile="me@x.com"), bus)
    job(NOW)
    assert set(bus.problems()) == {"anthropic"}


# --- extracting ----------------------------------------------------------------------------------

def extract_job(store, cfg, llm, bus, holder=None):
    holder = holder or ProfileHolder.from_file(cfg.profile_path)
    return ExtractJob(store, llm, holder, cfg, bus, log=lambda line: None, workers=1)


def test_an_empty_queue_calls_nothing(store, cfg):
    llm = SameReplyLLM(reply([item()]))
    assert extract_job(store, cfg, llm, EventBus())(NOW) is None and llm.calls == 0


def test_new_items_are_announced(store, cfg):
    add_mail(store, "m1")
    bus = EventBus()
    events = bus.subscribe()
    assert extract_job(store, cfg, SameReplyLLM(reply([item()])), bus)(NOW) is None
    assert drain(events) == [{"kind": "items_new", "new": 1, "merged": 0}]


def test_a_daily_cap_waits_until_midnight_in_hcmc(store, cfg):
    add_mail(store, "m1")
    capped = replace(cfg, llm=replace(cfg.llm, caps=Caps(5.0, 0.0001)))
    assert extract_job(store, capped, SameReplyLLM(reply([item()])), EventBus())(NOW) == timedelta(hours=7)


def test_a_busy_api_retries_in_5_minutes(store, cfg):
    add_mail(store, "m1")
    busy = LlmRejected("overloaded", kind="OverloadedError", status=529)
    assert extract_job(store, cfg, SameReplyLLM(reply([item()]), first=busy), EventBus())(NOW) == 5 * MIN


def test_a_rejected_request_waits_an_hour_and_says_why(store, cfg):
    add_mail(store, "m1")
    bad = LlmRejected("invalid x-api-key", kind="AuthenticationError", status=401)
    bus = EventBus()
    events = bus.subscribe()
    llm = SameReplyLLM(reply([item()]), first=bad)
    job = extract_job(store, cfg, llm, bus)
    assert job(NOW) == MIN  # called every minute, to notice a new key...
    assert job(NOW + 59 * MIN) == MIN and llm.calls == 1  # ...but the same key is not tried for an hour
    (event,) = drain(events)
    assert (event["kind"], event["source"], event["problem"]) == ("status", "anthropic", "anthropic")
    assert "invalid x-api-key" in event["detail"]


# --- the sweep and the profile -------------------------------------------------------------------

def test_the_sweep_deletes_old_bodies(store, cfg):
    add_mail(store, "m1")
    store.conn.execute("UPDATE mail_items SET inserted_at = datetime('now', '-8 days')")
    store.conn.commit()
    lines = []
    SweepJob(store, log=lines.append)(NOW)
    assert store.conn.execute("SELECT body_text FROM mail_items").fetchone()[0] == ""
    assert lines and "Hackathon" not in " ".join(lines)


def save_profile(cfg, text):
    before = cfg.profile_path.stat().st_mtime_ns
    cfg.profile_path.write_text(text, encoding="utf-8")
    os.utime(cfg.profile_path, ns=(before + 10**9, before + 10**9))  # a save always moves the time on


def seed_opportunity(store, holder):
    store.save_opportunity(opp(title="Backend Developer"), message_id="m1", source="devpost", flagged=False,
                           scorer=lambda o: score(o, holder.get())[0], today="2026-10-25")


def test_a_profile_edit_rescores_and_announces_it(store, cfg):
    holder = ProfileHolder.from_file(cfg.profile_path)
    seed_opportunity(store, holder)
    bus = EventBus()
    events = bus.subscribe()
    job = ProfileWatchJob(store, cfg, holder, bus, log=lambda line: None)
    assert job(NOW) is None and drain(events) == []  # nothing changed yet
    text = cfg.profile_path.read_text(encoding="utf-8")
    save_profile(cfg, text.replace("[penalize]", "[penalize]\nBackend = -40"))
    job(NOW)
    assert drain(events) == [{"kind": "ranking_changed", "rescored": 1}]
    assert holder.get().penalize["Backend"] == -40
    assert store.conn.execute("SELECT score FROM opportunities").fetchone()[0] == 10


def test_a_broken_profile_edit_keeps_the_old_profile_until_fixed(store, cfg):
    holder = ProfileHolder.from_file(cfg.profile_path)
    old, good = holder.get(), cfg.profile_path.read_text(encoding="utf-8")
    bus = EventBus()
    events = bus.subscribe()
    job = ProfileWatchJob(store, cfg, holder, bus, log=lambda line: None)
    save_profile(cfg, "[boost\nAI = ")
    assert job(NOW) is None
    (event,) = drain(events)
    assert (event["kind"], event["source"], event["problem"]) == ("status", "profile", "profile")
    assert "profile.toml" in event["detail"]
    assert holder.get() is old
    job(NOW)
    assert drain(events) == []  # reported once, not every 30 seconds
    save_profile(cfg, good.replace("[penalize]", "[penalize]\nBackend = -40"))
    job(NOW)
    assert [(e["kind"], e.get("source"), e.get("problem")) for e in drain(events)] == [
        ("status", "profile", None), ("ranking_changed", None, None)]  # the problem is over
    assert holder.get().penalize["Backend"] == -40 and bus.problems() == {}



# --- the extract job's waits (M1 review) ---------------------------------------------------------

class AlwaysBusy:
    def complete(self, request):
        raise LlmRejected("overloaded", kind="OverloadedError", status=529)


def test_new_mail_does_not_cut_a_wait_short(store, cfg):
    add_mail(store, "m1")
    llm = SameReplyLLM(reply([item()]), first=LlmRejected("overloaded", kind="OverloadedError", status=529))
    job = extract_job(store, cfg, llm, EventBus())
    assert job(NOW) == 5 * MIN
    assert job(NOW + MIN) == 4 * MIN and llm.calls == 1  # run_soon after new mail: still waiting
    assert job(NOW + 5 * MIN) is None and llm.calls == 2


def test_new_mail_after_the_daily_cap_books_nothing_until_midnight(store, cfg):
    add_mail(store, "m1")
    capped = replace(cfg, llm=replace(cfg.llm, caps=Caps(5.0, 0.0001)))
    job = extract_job(store, capped, SameReplyLLM(reply([item()])), EventBus())
    assert job(NOW) == timedelta(hours=7)
    assert job(NOW + MIN) == timedelta(hours=7) - MIN
    assert outcomes(store) == ["skipped_cap"]  # one row, not one per new email


def test_a_busy_api_backs_off_further_each_time(store, cfg):
    add_mail(store, "m1")
    job = extract_job(store, cfg, AlwaysBusy(), EventBus())
    waits = [job(NOW + m * MIN) for m in (0, 5, 20, 80, 140)]
    assert waits == [5 * MIN, 15 * MIN, 60 * MIN, 60 * MIN, 60 * MIN]


def test_a_clock_set_back_does_not_stretch_a_wait(store, cfg):
    add_mail(store, "m1")
    llm = SameReplyLLM(reply([item()]), first=LlmRejected("invalid x-api-key", kind="AuthenticationError",
                                                           status=401))
    job = extract_job(store, cfg, llm, EventBus())
    assert job(NOW) == MIN and llm.calls == 1
    assert job(NOW - timedelta(hours=3)) is None and llm.calls == 2  # not a 4-hour wait


def test_a_new_api_key_ends_the_wait_after_a_rejected_key(store, cfg):
    from assistant.llm import VaultLLM

    keys = ["old-key"]

    def build(key):
        if key == "old-key":
            return SameReplyLLM(reply([item()]), first=LlmRejected("invalid x-api-key", kind="AuthenticationError",
                                                                    status=401))
        return SameReplyLLM(reply([item()]))

    add_mail(store, "m1")
    bus = EventBus()
    job = extract_job(store, cfg, VaultLLM(load=lambda: keys[0], build=build), bus)
    assert job(NOW) == MIN
    assert job(NOW + MIN) == MIN  # the same key: look again in a minute
    keys[0] = "new-key"  # `assistant set-key anthropic` while the engine runs
    assert job(NOW + 2 * MIN) is None
    assert mail(store)["status"] == "extracted" and bus.problems() == {}  # the problem is over


def test_a_crash_mid_run_still_announces_saved_items_and_keeps_the_wait(store, cfg, monkeypatch):
    import assistant.engine.jobs as jobs
    from assistant.pipeline import RunReport

    add_mail(store, "m1")
    report = RunReport()
    report.count("new", 2)
    report.stop("busy", "Anthropic was busy (error 529)")

    def crash(*args, **kwargs):
        exc = RuntimeError("our bug")
        exc.report = report
        raise exc

    monkeypatch.setattr(jobs, "run_extraction", crash)
    bus = EventBus()
    events = bus.subscribe()
    job = extract_job(store, cfg, SameReplyLLM(reply([item()])), bus)
    with pytest.raises(RuntimeError):
        job(NOW)
    assert drain(events) == [{"kind": "items_new", "new": 2, "merged": 0}]
    assert job(NOW + MIN) == 4 * MIN


def test_the_extract_job_keeps_subjects_out_of_its_log_and_says_why_it_stopped(store, cfg):
    add_mail(store, "m1", subject="Your bank statement")
    add_mail(store, "m2", subject="Lab results")
    lines = []
    llm = SameReplyLLM(reply([item()]), first=LlmRejected("invalid x-api-key", kind="AuthenticationError",
                                                           status=401))
    ExtractJob(store, llm, ProfileHolder.from_file(cfg.profile_path), cfg, EventBus(), log=lines.append)(NOW)
    text = "\n".join(lines)
    assert "invalid x-api-key" in text  # what to fix, not only "error 401"
    assert "bank" not in text and "Lab" not in text


def test_a_cancelled_engine_starts_no_extraction(store, cfg):
    import threading

    add_mail(store, "m1")
    cancel = threading.Event()
    cancel.set()
    llm = SameReplyLLM(reply([item()]))
    job = ExtractJob(store, llm, ProfileHolder.from_file(cfg.profile_path), cfg, EventBus(), log=lambda line: None,
                     cancel=cancel)
    assert job(NOW) is None and llm.calls == 0


def test_a_half_saved_empty_profile_is_not_swapped_in(store, cfg):
    holder = ProfileHolder.from_file(cfg.profile_path)
    old, good = holder.get(), cfg.profile_path.read_text(encoding="utf-8")
    bus = EventBus()
    events = bus.subscribe()
    job = ProfileWatchJob(store, cfg, holder, bus, log=lambda line: None)
    save_profile(cfg, "")  # an editor that truncates, then writes
    job(NOW)
    assert holder.get() is old and drain(events) == []
    save_profile(cfg, good.replace("[penalize]", "[penalize]\nBackend = -40"))
    job(NOW)
    assert holder.get().penalize["Backend"] == -40


def test_a_good_run_starts_the_busy_waits_over(store, cfg):
    llm = AlwaysBusy()
    add_mail(store, "m1")
    job = extract_job(store, cfg, llm, EventBus())
    assert job(NOW) == 5 * MIN and job(NOW + 5 * MIN) == 15 * MIN
    job.llm = SameReplyLLM(reply([item()]))
    assert job(NOW + 20 * MIN) is None  # answered: the API is back
    add_mail(store, "m2")
    job.llm = AlwaysBusy()
    assert job(NOW + 21 * MIN) == 5 * MIN  # a new busy spell starts from 5 minutes again


def test_the_extract_job_names_extracted_emails_by_id(store, cfg):
    add_mail(store, "m1", subject="Your bank statement")
    lines = []
    ExtractJob(store, SameReplyLLM(reply([item()])), ProfileHolder.from_file(cfg.profile_path), cfg, EventBus(),
               log=lines.append)(NOW)
    text = "\n".join(lines)
    assert "m1" in text and "bank" not in text



# --- noticing a new sign-in, a new key and the network coming back (final review) ---------------

class SavedSignIn:
    """A stand-in for gmail_client.token_fingerprint: what `assistant auth` last saved."""

    def __init__(self):
        self.value = b"A"

    def __call__(self):
        return self.value


def test_a_new_gmail_sign_in_is_used_at_the_next_check(store, cfg):
    saved, built = SavedSignIn(), []

    def gmail():
        built.append(saved.value)
        return FakeGmail([], profile="me@x.com")

    job = CheckMailJob(store, gmail, cfg, EventBus(), Scheduler(FakeClock(NOW)), log=lambda line: None,
                       gmail_token=saved)
    job(NOW)
    job(NOW + 2 * MIN)
    assert built == [b"A"]  # a working service is kept...
    saved.value = b"B"  # `assistant auth` while the engine runs, before the old sign-in expires
    job(NOW + 4 * MIN)
    assert built == [b"A", b"B"]  # ...until a new sign-in is saved


def test_after_lost_access_a_new_sign_in_is_picked_up_within_a_minute(store, cfg):
    saved, built = SavedSignIn(), []

    def gmail():
        built.append(saved.value)
        if saved.value == b"A":
            raise GmailAuthError("Token refresh failed. Run: assistant auth")
        return FakeGmail([], profile="me@x.com")

    bus = EventBus()
    events = bus.subscribe()
    job = CheckMailJob(store, gmail, cfg, bus, Scheduler(FakeClock(NOW)), log=lambda line: None,
                       gmail_token=saved)
    assert job(NOW) == MIN
    assert job(NOW + MIN) == MIN and built == [b"A"]
    saved.value = b"B"
    assert job(NOW + 2 * MIN) is None and built == [b"A", b"B"]
    assert [(e["source"], e["problem"]) for e in drain(events)] == [("gmail", "auth_lost"), ("gmail", None)]


def test_an_offline_wait_ends_once_a_mail_check_gets_through(store, cfg):
    from assistant.llm import LlmUnreachable

    add_mail(store, "m1")
    store.set_state("last_ok_check_at", (NOW - MIN).isoformat())  # the last good check, before the outage
    llm = SameReplyLLM(reply([item()]), first=LlmUnreachable("no connection", kind="APIConnectionError"))
    job = extract_job(store, cfg, llm, EventBus())
    assert job(NOW) == MIN
    assert job(NOW + MIN) == MIN and llm.calls == 1  # still offline: look again in a minute
    store.set_state("last_ok_check_at", (NOW + 2 * MIN).isoformat())  # Wi-Fi is back
    assert job(NOW + 2 * MIN) is None and llm.calls == 2
    assert mail(store)["status"] == "extracted"


def test_an_offline_wait_keeps_growing_while_nothing_gets_through(store, cfg):
    from assistant.llm import LlmUnreachable

    class NoNetwork:
        def __init__(self):
            self.tried_at, self.minute = [], None

        def complete(self, request):
            self.tried_at.append(self.minute)
            raise LlmUnreachable("no connection", kind="APIConnectionError")

    add_mail(store, "m1")
    llm = NoNetwork()
    job = extract_job(store, cfg, llm, EventBus())
    for minute in range(81):  # the job looks every minute; it tries the network again after 5, 15, 60
        llm.minute = minute
        job(NOW + minute * MIN)
    assert llm.tried_at == [0, 5, 20, 80]


def test_a_sign_in_saved_again_while_building_does_not_rebuild_every_check(store, cfg):
    saved, built = SavedSignIn(), []

    def gmail():  # get_credentials saves the sign-in again; Google may have rotated it
        built.append(1)
        saved.value = b"A, refreshed"
        return FakeGmail([], profile="me@x.com")

    job = CheckMailJob(store, gmail, cfg, EventBus(), Scheduler(FakeClock(NOW)), log=lambda line: None,
                       gmail_token=saved)
    job(NOW)
    job(NOW + 2 * MIN)
    assert len(built) == 1


def ticks(clock, sched, minutes):
    for _ in range(minutes * 4):  # the engine polls every 15 seconds
        clock.advance(timedelta(seconds=15))
        sched.tick()


def test_the_scheduler_notices_a_new_api_key_within_a_minute_or_two(store, cfg):
    from assistant.llm import VaultLLM

    keys = ["old-key"]

    def build(key):
        if key == "old-key":
            return SameReplyLLM(reply([item()]), first=LlmRejected("invalid x-api-key", kind="AuthenticationError",
                                                                    status=401))
        return SameReplyLLM(reply([item()]))

    add_mail(store, "m1")
    clock = FakeClock(NOW)
    sched = Scheduler(clock)
    sched.add("extract", MIN, extract_job(store, cfg, VaultLLM(load=lambda: keys[0], build=build), EventBus()))
    sched.tick()  # the key is rejected: a wait begins
    keys[0] = "new-key"
    ticks(clock, sched, 2)
    assert mail(store)["status"] == "extracted"


def test_the_scheduler_resumes_extraction_soon_after_the_network_returns(store, cfg):
    from assistant.llm import LlmUnreachable

    add_mail(store, "m1")
    llm = SameReplyLLM(reply([item()]), first=LlmUnreachable("no connection", kind="APIConnectionError"))
    clock = FakeClock(NOW)
    sched = Scheduler(clock)
    sched.add("extract", MIN, extract_job(store, cfg, llm, EventBus()))
    sched.tick()  # offline: a wait begins
    store.set_state("last_ok_check_at", (NOW + MIN).isoformat())  # a mail check gets through
    ticks(clock, sched, 2)
    assert mail(store)["status"] == "extracted"


# --- a status for every reason extraction stops --------------------------------------------------

@pytest.mark.parametrize("failure, problem, emails", [
    (LlmRejected("overloaded", kind="OverloadedError", status=529), "busy", 1),
    (LlmNoAnswer("timed out", kind="APITimeoutError"), "no_answer", 3),
    (LlmUnreachable("no connection", kind="APIConnectionError"), "offline", 1),
])
def test_every_extraction_stop_is_reported(store, cfg, failure, problem, emails):
    class Failing:
        def complete(self, request):
            raise failure

    for n in range(emails):
        add_mail(store, f"m{n}")
    bus = EventBus()
    events = bus.subscribe()
    extract_job(store, cfg, Failing(), bus)(NOW)
    assert bus.problems()["anthropic"]["problem"] == problem
    assert [(e["source"], e["problem"]) for e in drain(events)] == [("anthropic", problem)]


def test_the_daily_cap_says_until_when(store, cfg):
    add_mail(store, "m1")
    capped = replace(cfg, llm=replace(cfg.llm, caps=Caps(5.0, 0.0001)))
    bus = EventBus()
    extract_job(store, capped, SameReplyLLM(reply([item()])), bus)(NOW)
    assert bus.problems()["anthropic"] == {"problem": "cap_daily", "until": "2026-10-25T17:00:00+00:00"}


def test_a_good_run_clears_the_reported_stop(store, cfg):
    add_mail(store, "m1")
    bus = EventBus()
    llm = SameReplyLLM(reply([item()]), first=LlmRejected("overloaded", kind="OverloadedError", status=529))
    job = extract_job(store, cfg, llm, bus)
    job(NOW)
    assert "anthropic" in bus.problems()
    job(NOW + 5 * MIN)
    assert bus.problems() == {}


def test_a_run_that_stops_at_the_call_limit_reports_nothing(store, cfg):
    for n in range(61):
        add_mail(store, f"m{n}")
    bus = EventBus()
    llm = SameReplyLLM(reply([item()]))
    extract_job(store, cfg, llm, bus)(NOW)
    assert llm.calls == 60 and bus.problems() == {}  # normal: the next run takes the rest
