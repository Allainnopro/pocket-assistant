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
from test_pipeline import add_mail, item, reply
from test_rank import opp
from test_scheduler import FakeClock

from assistant.config import load_config
from assistant.costs import Caps
from assistant.engine.events import ITEMS_NEW, RANKING_CHANGED, STATUS, EventBus
from assistant.engine.jobs import CheckMailJob, ExtractJob, ProfileHolder, ProfileWatchJob, SweepJob
from assistant.engine.scheduler import Scheduler
from assistant.errors import GmailAuthError
from assistant.llm import LlmRejected
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
    assert drain(events) == [{"kind": "status", "online": True, "last_ok_check_at": NOW.isoformat(),
                              "problem": None}]


def test_no_new_mail_leaves_extraction_on_its_schedule(store, cfg):
    job, sched = check_job(store, cfg, lambda: FakeGmail([], profile="me@x.com"), EventBus())
    sched.tick()
    job(NOW)
    assert sched.tick() == []


class FlakyGmail(FakeGmail):
    """Loses the network in the middle of a check."""

    def users(self):
        raise OSError("connection reset")


def test_offline_backs_off_5_then_15_minutes_then_recovers(store, cfg):
    built = []
    services = iter([OSError("no route to host"), FlakyGmail([], profile="me@x.com"), new_mail_gmail()])

    def gmail():
        built.append(1)
        service = next(services)
        if isinstance(service, BaseException):
            raise service
        return service

    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, gmail, bus)
    assert job(NOW) == 5 * MIN
    assert job(NOW) == 15 * MIN  # a service that failed mid-check is dropped and built again
    assert job(NOW) is None
    assert job(NOW) is None and len(built) == 3  # a working service is kept
    problems = [e["problem"] for e in drain(events)]
    assert problems == ["offline", "offline", None, None]
    assert store.get_state("last_check_error") is None


def test_lost_gmail_access_is_reported_and_retried_in_15_minutes(store, cfg):
    def gmail():
        raise GmailAuthError("Token refresh failed. Run: assistant auth")

    bus = EventBus()
    events = bus.subscribe()
    job, _ = check_job(store, cfg, gmail, bus)
    assert job(NOW) == 15 * MIN
    assert [e["problem"] for e in drain(events)] == ["auth_lost"]
    assert store.get_state("last_check_error") == "auth_lost"


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
    assert extract_job(store, cfg, SameReplyLLM(reply([item()]), first=bad), bus)(NOW) == 60 * MIN
    (event,) = drain(events)
    assert (event["kind"], event["problem"]) == ("status", "anthropic") and "invalid x-api-key" in event["detail"]


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
    assert (event["kind"], event["problem"]) == ("status", "profile") and "profile.toml" in event["detail"]
    assert holder.get() is old
    job(NOW)
    assert drain(events) == []  # reported once, not every 30 seconds
    save_profile(cfg, good.replace("[penalize]", "[penalize]\nBackend = -40"))
    job(NOW)
    assert [e["kind"] for e in drain(events)] == ["ranking_changed"]
    assert holder.get().penalize["Backend"] == -40
