"""The opportunities table: merging sightings, rescoring, listing.

Run: pytest
"""

import itertools
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_rank import PROFILE, opp

from assistant.extraction import LOCATION_SCOPES
from assistant.models import OPPORTUNITY_KINDS
from assistant.rank import score
from assistant.store import Store

ROOT = Path(__file__).parent.parent


def scorer(o):
    return score(o, PROFILE)[0]


TODAY = "2026-10-16"


def save(store, item, message_id="m1", flagged=False, today=TODAY):
    return store.save_opportunity(item, message_id=message_id, source="devpost", flagged=flagged, scorer=scorer,
                                  today=today)


def rows(store):
    return [dict(r) for r in store.conn.execute("SELECT * FROM opportunities ORDER BY id")]


def test_the_table_accepts_exactly_the_kinds_and_locations_the_model_can_return(tmp_path):
    store = Store(tmp_path / "a.db")
    for i, kind in enumerate(OPPORTUNITY_KINDS):
        save(store, opp(kind=kind, title=f"kind {i}"))
    for i, scope in enumerate(LOCATION_SCOPES):
        save(store, opp(location_scope=scope, title=f"scope {i}"))
    assert len(rows(store)) == len(OPPORTUNITY_KINDS) + len(LOCATION_SCOPES)
    for column, bad in (("kind", "party"), ("location_scope", "moon")):
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(f"UPDATE opportunities SET {column} = ? WHERE id = 1", (bad,))


def test_a_new_opportunity_is_stored_with_its_score(tmp_path):
    store = Store(tmp_path / "a.db")
    assert save(store, opp(title="AI Engineer", location_scope="hcmc")) == (1, False)
    (row,) = rows(store)
    # relevance 50 + AI 20 + at home (HCMC) 20
    assert (row["score"], row["seen_count"], row["status"], row["first_message_id"]) == (90, 1, "active", "m1")


def test_an_undated_sighting_and_a_dated_one_merge_and_the_deadline_fills_in(tmp_path):
    store = Store(tmp_path / "a.db")
    save(store, opp(relevance=60), "m1")
    assert save(store, opp(deadline="2026-11-20", relevance=70, url="https://x.example.com"), "m2") == (1, True)
    (row,) = rows(store)
    assert (row["deadline"], row["relevance"], row["url"], row["seen_count"]) == (
        "2026-11-20", 70, "https://x.example.com", 2)
    assert row["dedupe_key"].endswith("|2026-11-20") and row["first_message_id"] == "m1"


def test_two_different_deadlines_are_two_editions(tmp_path):
    store = Store(tmp_path / "a.db")
    save(store, opp(deadline="2026-06-01"))
    save(store, opp(deadline="2026-12-01"))
    assert [r["deadline"] for r in rows(store)] == ["2026-06-01", "2026-12-01"]


@pytest.mark.parametrize("deadlines", [
    (None, "2026-11-01", None, "2026-11-01"),
    ("2026-11-01", "2026-12-01", None, None),
])
def test_no_order_of_four_sightings_breaks_the_unique_key(tmp_path, deadlines):
    # The plan's critic found an order of four emails that crashed on the UNIQUE key
    for n, order in enumerate(set(itertools.permutations(deadlines))):
        store = Store(tmp_path / f"{n}.db")
        for i, deadline in enumerate(order):
            save(store, opp(deadline=deadline), f"m{i}")
        stored = rows(store)
        assert sum(r["seen_count"] for r in stored) == 4
        assert {r["deadline"] for r in stored} <= set(deadlines)
        store.close()


def test_an_undated_announcement_never_vanishes_into_a_past_edition(tmp_path):
    store = Store(tmp_path / "a.db")
    save(store, opp(title="FPT Hackathon", kind="hackathon", deadline="2025-11-01"), "m1", today="2025-10-01")
    store.rescore_opportunities(scorer, TODAY)                      # last year's edition is now expired
    assert save(store, opp(title="FPT Hackathon", kind="hackathon", relevance=90), "m2") == (2, False)
    assert [r["title"] for r in store.active_opportunities(TODAY)] == ["FPT Hackathon"]


def test_an_emails_items_and_its_queue_status_are_saved_together_or_not_at_all(tmp_path):
    from assistant.models import MailItem

    store = Store(tmp_path / "a.db")
    store.add(MailItem(message_id="m1", thread_id="t", received_at="2026-10-16T02:00:00Z", sender="a@example.com",
                       sender_domain="example.com", to_me=True, recipient_count=1, subject="s", snippet="",
                       body_text="body", source="devpost", labels=[]))
    store.commit()
    exploding = iter([0])

    def scorer_that_crashes(o):  # Ctrl+C after the first item
        if next(exploding, None) is None:
            raise KeyboardInterrupt
        return scorer(o)

    with pytest.raises(KeyboardInterrupt):
        store.save_extraction("m1", [opp(title="AI Engineer"), opp(title="Data Intern")], source="devpost",
                              flagged=False, scorer=scorer_that_crashes, today=TODAY, status="extracted")
    assert rows(store) == [] and store.conn.execute("SELECT status FROM mail_items").fetchone()[0] == "new"
    store.save_extraction("m1", [opp(title="AI Engineer"), opp(title="Data Intern")], source="devpost",
                          flagged=False, scorer=scorer, today=TODAY, status="extracted")
    assert [r["seen_count"] for r in rows(store)] == [1, 1]  # the retry counted nothing twice
    assert store.conn.execute("SELECT status, body_text FROM mail_items").fetchone()[:] == ("extracted", "")


def test_two_writers_saving_the_same_new_item_dont_collide(tmp_path, monkeypatch):
    import threading

    db = tmp_path / "a.db"
    Store(db).close()
    # Force the dangerous interleaving: each writer waits after its first lookup for
    # the other to look too. With the write lock taken first, the second can't get
    # there; the wait times out and they run in turn (one inserts, one merges).
    both_looked = threading.Barrier(2, timeout=0.5)
    real_one, first_lookup = Store._one, threading.local()

    def one_then_wait(self, sql, *params):
        found = real_one(self, sql, *params)
        if not getattr(first_lookup, "done", False):
            first_lookup.done = True
            try:
                both_looked.wait()
            except threading.BrokenBarrierError:
                pass
        return found

    monkeypatch.setattr(Store, "_one", one_then_wait)
    errors = []

    def writer(n):
        store = Store(db)
        try:
            save(store, opp(title="AI Engineer"), f"m{n}")
        except Exception as exc:  # an IntegrityError here is the bug
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    store = Store(db)
    assert [r["seen_count"] for r in rows(store)] == [2]


def test_a_flag_sticks_once_any_sighting_addressed_the_ai(tmp_path):
    store = Store(tmp_path / "a.db")
    save(store, opp(), flagged=True)
    save(store, opp(), flagged=False)
    assert rows(store)[0]["flagged"] == 1


# --- commands -----------------------------------------------------------------------------------

NOW = datetime(2026, 10, 16, 3, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    from assistant.config import load_config

    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "profile.example.toml", tmp_path / "profile.toml")
    return load_config(tmp_path / "config.toml")


def test_opportunities_lists_open_ones_best_first_with_the_reasons(cfg, capsys):
    from assistant.cli import show_opportunities

    store = Store(cfg.db_path)
    save(store, opp(title="AI Intern", location_scope="hcmc", url="https://a.example.com"), "m1", flagged=True)
    save(store, opp(title="Backend Developer", location_scope="vietnam_other", location="Hanoi"), "m2")
    save(store, opp(title="Closed Hackathon", kind="hackathon", deadline="2026-10-15"), "m3")
    store.close()
    ranked = show_opportunities(cfg, top=10, now_utc=NOW)
    out = capsys.readouterr().out
    assert [r["title"] for _, r in ranked] == ["AI Intern", "Backend Developer"]  # yesterday's deadline is gone
    # profile.example.toml: AI +15, Internship +15, HCMC +10 at home, -30 outside it
    assert "relevance 50, +15 AI, +15 Internship, +10 at home (HCMC)" in out
    assert "relevance 50, -30 elsewhere in Vietnam" in out
    assert "[its email addressed the AI]" in out and "https://a.example.com" in out


def test_rescore_follows_a_profile_edit_and_expires_past_deadlines(cfg, capsys):
    from assistant.cli import rescore

    store = Store(cfg.db_path)
    save(store, opp(title="Backend Developer"), "m1")
    save(store, opp(title="Old Hackathon", kind="hackathon", deadline="2026-10-01"), "m2")
    store.close()
    profile = cfg.profile_path.read_text(encoding="utf-8")
    cfg.profile_path.write_text(profile.replace("[penalize]", "[penalize]\nBackend = -40"), encoding="utf-8")
    assert rescore(cfg, now_utc=NOW) == (2, 1)
    store = Store(cfg.db_path)
    stored = {r["title"]: (r["score"], r["status"]) for r in rows(store)}
    assert stored == {"Backend Developer": (10, "active"), "Old Hackathon": (50, "expired")}


def test_an_empty_list_says_where_opportunities_come_from(cfg, capsys):
    from assistant.cli import show_opportunities

    assert show_opportunities(cfg, top=5, now_utc=NOW) == []
    assert "assistant extract" in capsys.readouterr().out


def queued_mail(store, message_id):
    from assistant.models import MailItem

    store.add(MailItem(message_id=message_id, thread_id="t", received_at="2026-10-16T02:00:00Z",
                       sender="a@example.com", sender_domain="example.com", to_me=True, recipient_count=1,
                       subject="s", snippet="", body_text="body", source="devpost", labels=[]))
    store.commit()


def test_save_extraction_records_every_sighting_and_the_needs_action_note(tmp_path):
    store = Store(tmp_path / "a.db")
    queued_mail(store, "m1")
    queued_mail(store, "m2")
    store.save_extraction("m1", [opp(title="AI Engineer"), opp(title="Data Intern")], source="devpost",
                          flagged=False, scorer=scorer, today=TODAY, status="extracted",
                          needs_action=True, action_note="Register by 15 Nov")
    store.save_extraction("m2", [opp(title="AI Engineer")], source="devpost", flagged=False, scorer=scorer,
                          today=TODAY, status="extracted")
    assert {tuple(r) for r in store.conn.execute("SELECT opportunity_id, message_id FROM sightings")} == {
        (1, "m1"), (2, "m1"), (1, "m2")}
    assert tuple(store.conn.execute(
        "SELECT needs_action, action_note FROM mail_items WHERE message_id = 'm1'").fetchone()) == (1, "Register by 15 Nov")
    assert tuple(store.conn.execute(
        "SELECT needs_action, action_note FROM mail_items WHERE message_id = 'm2'").fetchone()) == (0, None)


def test_save_extraction_stores_the_ais_kind_and_urgency(tmp_path):
    store = Store(tmp_path / "a.db")
    queued_mail(store, "m1")
    queued_mail(store, "m2")
    store.save_extraction("m1", [], source="other", flagged=False, scorer=scorer, today=TODAY, status="no_items",
                          ai_kind="news_events", ai_urgent=True)
    store.save_extraction("m2", [], source="other", flagged=False, scorer=scorer, today=TODAY, status="no_items")
    assert {r[0]: (r[1], r[2]) for r in store.conn.execute("SELECT message_id, ai_kind, ai_urgent FROM mail_items")} == {
        "m1": ("news_events", 1), "m2": (None, 0)}
