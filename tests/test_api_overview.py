"""The first screen, the reports and the settings a dashboard shows.

Run: pytest
"""

from datetime import datetime, timezone

import pytest
from api_helpers import PASSWORD, Api, api, bearer  # noqa: F401
from test_api_opportunities import seed
from test_engine_jobs import drain

from assistant.engine.events import RANKING_CHANGED


@pytest.fixture
def api_at(tmp_path):
    made = []

    def start(now: datetime) -> Api:
        made.append(Api(tmp_path, now=now))
        return made[-1]

    yield start
    for one in made:
        one.close()


def call(api, day, cost, purpose="extract", tokens=(1000, 200), when="00:00:00"):
    api.store.conn.execute(
        """INSERT INTO llm_calls (ts_utc, month_local, day_local, purpose, essential, model_requested, input_tokens,
                                  output_tokens, cost_usd, price_version, outcome)
           VALUES (?, ?, ?, ?, 1, 'claude-haiku-5-5', ?, ?, ?, 'test', 'ok')""",
        (f"{day}T{when}+00:00", day[:7], day, purpose, *tokens, cost))
    api.store.conn.commit()


def test_today_counts_follow_the_hcmc_day(api_at):  # Review Focus 5
    api = api_at(datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc))  # 00:30 on the 28th in HCMC
    seed(api, "New today", first_seen_at="2026-10-27 17:10:00")
    seed(api, "Yesterday", first_seen_at="2026-10-27 16:50:00")
    seed(api, "Due in 6 days", deadline="2026-11-03")
    seed(api, "Due in 7 days", deadline="2026-11-04")
    counts = api.get("/api/today")["counts"]
    assert counts["new_today"] == 1
    assert counts["deadlines_this_week"] == 1


def test_today_brings_everything_the_first_screen_needs(api):
    for n in range(7):
        seed(api, f"Pick {n}", score=90 - n)
    seed(api, "Starred one", score=10, starred=1)
    seed(api, "Applied one", status="applied")
    seed(api, "Due soon", deadline="2026-11-05", score=10)
    call(api, "2026-11-03", 0.25)
    call(api, "2026-11-01", 0.5)
    today = api.get("/api/today")
    assert set(today) == {"counts", "top_picks", "needs_action", "upcoming_deadlines", "spend", "status"}
    assert [p["title"] for p in today["top_picks"]] == [f"Pick {n}" for n in range(5)]
    assert today["counts"]["starred"] == 1 and today["counts"]["applied"] == 1
    assert [d["title"] for d in today["upcoming_deadlines"]] == ["Due soon"]
    assert today["spend"] == {"month_usd": 0.75, "today_usd": 0.25, "monthly_soft_cap_usd": api.cfg.llm.caps.monthly_soft_usd,
                              "daily_cap_usd": api.cfg.llm.caps.daily_usd}


def test_today_lists_recent_emails_that_ask_for_action(api):
    from test_pipeline import add_mail

    add_mail(api.store, "m-new")
    add_mail(api.store, "m-old")
    add_mail(api.store, "m-none")
    api.store.conn.execute("UPDATE mail_items SET needs_action = 1, action_note = 'reply by Friday', "
                           "received_at = '2026-11-02T08:00:00+00:00' WHERE message_id = 'm-new'")
    api.store.conn.execute("UPDATE mail_items SET needs_action = 1, received_at = '2026-10-20T08:00:00+00:00' "
                           "WHERE message_id = 'm-old'")
    api.store.conn.commit()
    needs = api.get("/api/today")["needs_action"]
    assert [(n["message_id"], n["action_note"]) for n in needs] == [("m-new", "reply by Friday")]
    assert set(needs[0]) == {"message_id", "subject", "action_note", "received_at"}


def test_today_shows_the_engines_problems(api):
    api.bus.report("anthropic", "busy", detail="retrying in 5 min")
    api.store.set_state("last_check_error", "offline")
    status = api.get("/api/today")["status"]
    assert status["problems"] == {"anthropic": {"problem": "busy", "detail": "retrying in 5 min"}}
    assert status["last_check_error"] == "offline"


def test_spend_report_groups_calls_by_hcmc_day(api):
    call(api, "2026-11-02", 0.25, tokens=(1000, 100))
    call(api, "2026-11-02", 0.5, tokens=(2000, 300))
    call(api, "2026-11-03", 0.125)
    call(api, "2026-08-01", 9.0)  # outside the 30 days
    report = api.get("/api/reports/spend", days=30)
    assert report["days"] == [
        {"day": "2026-11-02", "cost_usd": 0.75, "calls": 2, "input_tokens": 3000, "output_tokens": 400},
        {"day": "2026-11-03", "cost_usd": 0.125, "calls": 1, "input_tokens": 1000, "output_tokens": 200}]
    assert report["month_usd"] == 0.875 and report["monthly_soft_cap_usd"] == api.cfg.llm.caps.monthly_soft_usd
    assert api.client.get("/api/reports/spend", params={"days": 0}, headers=bearer(PASSWORD)).status_code == 422


def test_digests_report_is_empty_until_there_are_digests(api):
    assert api.get("/api/reports/digests") == {"digests": []}
    api.store.conn.execute("INSERT INTO digests (digest_date, text, html, item_ids) VALUES ('2026-11-02', 't', 'h', '[1, 2]')")
    api.store.conn.commit()
    [digest] = api.get("/api/reports/digests")["digests"]
    assert digest["digest_date"] == "2026-11-02" and digest["item_count"] == 2 and digest["sent_at"] is None


def test_items_report_counts_by_kind_and_source_per_week(api):
    # NOW is Tuesday 2026-11-03 in HCMC: this week starts Monday 2026-11-02
    seed(api, "a", kind="hackathon", first_seen_at="2026-11-02 01:00:00")
    b = seed(api, "b", kind="internship", first_seen_at="2026-11-03 02:00:00")
    api.store.conn.execute("UPDATE opportunities SET source = 'linkedin' WHERE id = ?", (b,))
    api.store.conn.commit()
    seed(api, "c", kind="hackathon", first_seen_at="2026-11-01 16:30:00")  # 23:30 Sunday in HCMC: last week
    seed(api, "d", kind="seminar", first_seen_at="2026-11-01 17:30:00")    # still Sunday in UTC, Monday in HCMC
    weeks = api.get("/api/reports/items", weeks=2)["weeks"]
    assert weeks == [
        {"week_start": "2026-10-26", "by_kind": {"hackathon": 1}, "by_source": {"devpost": 1}},
        {"week_start": "2026-11-02", "by_kind": {"hackathon": 1, "internship": 1, "seminar": 1},
         "by_source": {"devpost": 2, "linkedin": 1}}]


def test_settings_report_connections_without_revealing_anything(api, vault):
    from assistant.vault import save

    key = "sk-ant-api03-" + "k" * 60
    save("anthropic", key)
    settings = api.get("/api/settings")
    assert settings["connections"]["anthropic"] == {"key_saved": True}
    assert settings["connections"]["gmail"]["signed_in"] is False  # no App Password saved
    assert settings["connections"]["telegram"] == {"paired": False}
    assert settings["for_you_min_score"] == api.cfg.dashboard.for_you_min_score
    assert set(settings["profile"]) == {"level", "interests", "open_to", "home", "remote_ok"}
    assert key not in api.client.get("/api/settings", headers=bearer(PASSWORD)).text


def test_gmail_counts_as_signed_in_only_while_access_works(api):
    from assistant.vault import save

    save("gmail", "abcdefghijklmnop")
    assert api.get("/api/settings")["connections"]["gmail"]["signed_in"] is True
    api.store.set_state("last_check_error", "auth_lost")
    assert api.get("/api/settings")["connections"]["gmail"]["signed_in"] is False


def test_rescore_uses_the_current_profile_and_announces_it(api):
    item = seed(api, "Stale score", score=1)
    seed(api, "Past", deadline="2026-11-01")
    events = api.bus.subscribe()
    r = api.client.post("/api/settings/rescore", headers=bearer(PASSWORD))
    assert r.status_code == 200 and r.json() == {"rescored": 2, "expired": 1}
    assert api.store.conn.execute("SELECT score FROM opportunities WHERE id = ?", (item,)).fetchone()[0] != 1
    assert {"kind": RANKING_CHANGED, "rescored": 2} in drain(events)
