"""The opportunities a dashboard shows, and what you can do to them.

Run: pytest
"""

import time
from datetime import datetime, timezone

import pytest
from api_helpers import PASSWORD, TAILNET, api, api_at, bearer, tailnet_api  # noqa: F401
from test_pipeline import add_mail

from assistant.engine.events import ITEM_CHANGED


def seed(api, title, score=70, kind="internship", status="active", deadline=None, organizer="Example Co", **more):
    columns = dict(dedupe_key=f"{title}|{deadline}", base_key=title, kind=kind, title=title, organizer=organizer,
                   deadline=deadline, location_scope="hcmc", relevance=50, score=score, why="fits", source="devpost",
                   first_message_id="m0", status=status, **more)
    names = ", ".join(columns)
    cur = api.store.conn.execute(f"INSERT INTO opportunities ({names}) VALUES ({', '.join('?' * len(columns))})",
                                 tuple(columns.values()))
    api.store.conn.commit()
    return cur.lastrowid


def titles(api, **params):
    return [i["title"] for i in api.get("/api/opportunities", **params)["items"]]


def post(api, item, action):
    return api.client.post(f"/api/opportunities/{item}/{action}", headers=bearer(PASSWORD))


def test_for_you_lists_open_items_above_the_threshold_best_first(api):
    seed(api, "Seventy", 70)
    seed(api, "Ninety", 90)
    seed(api, "Sixty", 60)
    seed(api, "Below", 59)
    seed(api, "Dismissed", 95, status="dismissed")
    seed(api, "Expired", 99, status="expired")
    seed(api, "Past deadline", 98, deadline="2026-11-02")
    assert titles(api, tab="for_you") == ["Ninety", "Seventy", "Sixty"]
    assert titles(api) == ["Ninety", "Seventy", "Sixty"]  # the default tab


def test_a_deadline_today_is_still_open(api):
    seed(api, "Today", 70, deadline="2026-11-03")
    assert titles(api) == ["Today"]


def test_equal_scores_put_the_nearest_deadline_first_and_undated_last(api):
    seed(api, "Undated", 70)
    seed(api, "Later", 70, deadline="2026-12-01")
    seed(api, "Sooner", 70, deadline="2026-11-10")
    assert titles(api) == ["Sooner", "Later", "Undated"]


def test_all_shows_low_scores_but_not_closed_items(api):
    seed(api, "Low", 10)
    seed(api, "Expired", 99, status="expired")
    seed(api, "Dismissed", 99, status="dismissed")
    assert titles(api, tab="all") == ["Low"]


def test_filters_narrow_a_tab_and_tab_total_counts_what_they_hide(api):
    seed(api, "Hack one", kind="hackathon", deadline="2026-11-05")
    seed(api, "Job far", kind="job", deadline="2027-01-01", score=90)
    seed(api, "Job near", kind="job", deadline="2026-11-04", organizer="Acme Labs")
    assert titles(api, kind="hackathon") == ["Hack one"]
    assert titles(api, deadline_within=7) == ["Job near", "Hack one"]  # same score: the nearer deadline first
    assert titles(api, min_score=80) == ["Job far"]
    assert titles(api, q="acme") == ["Job near"]
    got = api.get("/api/opportunities", kind="hackathon")
    assert (got["tab_total"], got["total"]) == (3, 1)


def test_limit_and_offset_page_through_a_tab(api):
    for n in range(5):
        seed(api, f"Item {n}", 90 - n)
    got = api.get("/api/opportunities", limit=2, offset=1)
    assert [i["title"] for i in got["items"]] == ["Item 1", "Item 2"] and got["total"] == 5


@pytest.mark.parametrize("params", [{"tab": "nope"}, {"limit": 0}, {"limit": 1001}, {"deadline_within": -1},
                                    {"q": "x" * 101}, {"offset": -1}])
def test_nonsense_queries_are_422(api, params):
    assert api.client.get("/api/opportunities", params=params, headers=bearer(PASSWORD)).status_code == 422


@pytest.mark.parametrize("q", ["%", "_", "it's", '"x"', "back\\slash"])
def test_a_filter_treats_wildcards_and_quotes_literally(api, q):
    seed(api, "Plain title")
    assert titles(api, q=q) == []
    seed(api, f"Has {q} inside")
    assert titles(api, q=q) == [f"Has {q} inside"]


def test_a_row_carries_what_the_table_needs(api):
    seed(api, "Row", seen_count=3, url="https://x.example.com", flagged=1)
    row = api.get("/api/opportunities")["items"][0]
    assert {"id", "kind", "title", "organizer", "deadline", "location", "location_scope", "score", "why", "seen_count",
            "starred", "status", "url", "flagged", "source", "first_seen_at"} == set(row)
    assert (row["seen_count"], row["flagged"], row["starred"]) == (3, True, False)


def test_detail_explains_the_score_against_the_current_profile(api):
    item = seed(api, "AI Engineer Intern")
    got = api.get(f"/api/opportunities/{item}")
    # profile.example.toml: AI +15, Internship +15, HCMC +10 at home; relevance 50
    assert {"label": "relevance", "points": 50} in got["breakdown"]
    assert {"label": "AI", "points": 15} in got["breakdown"]
    assert got["explanation"].startswith("relevance 50, ")
    assert {"eligibility", "prize_or_pay"} <= set(got)


def test_detail_lists_each_sighting_with_its_gmail_link_and_no_address(api):
    item = seed(api, "Seen twice")
    add_mail(api.store, "m1", subject="First email", sender="Job Alerts <jobs@example.com>")
    add_mail(api.store, "m2", subject="Second email", sender="plain@example.com", domain="example.com")
    api.store.conn.executemany("INSERT INTO sightings (opportunity_id, message_id) VALUES (?, ?)",
                               [(item, "m1"), (item, "m2")])
    api.store.conn.commit()
    api.store.set_state("gmail_address", "me+tag@gmail.com")
    got = api.get(f"/api/opportunities/{item}")
    sightings = {s["message_id"]: s for s in got["sightings"]}
    assert sightings["m1"]["subject"] == "First email"
    assert sightings["m1"]["sender_name"] == "Job Alerts"
    assert sightings["m2"]["sender_name"] == "example.com"  # never the address
    assert sightings["m1"]["gmail_url"] == "https://mail.google.com/mail/?authuser=me%2Btag%40gmail.com#all/m1"


def test_actions_move_items_between_tabs_and_are_recorded(api):
    item = seed(api, "Mover")
    assert post(api, item, "star").json()["starred"] is True
    assert titles(api, tab="starred") == ["Mover"]
    assert post(api, item, "unstar").json()["starred"] is False
    assert titles(api, tab="starred") == []
    assert post(api, item, "dismiss").json()["status"] == "dismissed"
    assert titles(api) == [] and titles(api, tab="dismissed") == ["Mover"]
    assert post(api, item, "restore").json()["status"] == "active"
    assert post(api, item, "applied").json()["status"] == "applied"
    assert titles(api) == [] and titles(api, tab="applied") == ["Mover"]
    assert post(api, item, "restore").json()["status"] == "active"
    recorded = [r[0] for r in api.store.conn.execute("SELECT action FROM item_actions ORDER BY id")]
    assert recorded == ["star", "unstar", "dismiss", "restore", "applied", "restore"]


def test_restoring_an_item_past_its_deadline_files_it_as_expired(api):
    item = seed(api, "Old", status="dismissed", deadline="2026-11-02")
    assert post(api, item, "restore").json()["status"] == "expired"


def test_an_action_is_announced_to_every_listener(api):
    item = seed(api, "Heard")
    events = api.bus.subscribe()
    post(api, item, "star")
    assert events.get_nowait() == {"kind": ITEM_CHANGED, "id": item, "action": "star"}


def test_a_paired_device_can_read_and_act(tailnet_api):
    item = seed(tailnet_api, "From the laptop")
    _, key = tailnet_api.pair()
    h = {"Host": TAILNET, **bearer(key)}
    assert tailnet_api.client.get("/api/opportunities", headers=h).status_code == 200
    assert tailnet_api.client.post(f"/api/opportunities/{item}/star", headers=h).json()["starred"] is True


def test_every_route_needs_a_secret(api):
    item = seed(api, "Guarded")
    assert api.client.get("/api/opportunities").status_code == 401
    assert api.client.get(f"/api/opportunities/{item}").status_code == 401
    assert api.client.post(f"/api/opportunities/{item}/star").status_code == 401


def test_an_unknown_item_or_action_is_404_or_422(api):
    assert api.client.get("/api/opportunities/99", headers=bearer(PASSWORD)).status_code == 404
    assert post(api, 99, "star").status_code == 404
    item = seed(api, "Real")
    assert post(api, item, "delete").status_code == 422


def test_five_thousand_items_list_fast(api):
    rows = [(f"k{n}", f"b{n}", "job", f"Item {n}", "hcmc", 50, n % 150, "fits", "devpost", "m0") for n in range(5000)]
    api.store.conn.executemany(
        """INSERT INTO opportunities (dedupe_key, base_key, kind, title, location_scope, relevance, score, why, source,
               first_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    api.store.conn.commit()
    start = time.perf_counter()
    got = api.get("/api/opportunities", tab="all", limit=200)
    assert time.perf_counter() - start < 0.25  # the target is 50 ms; headroom for a busy machine
    assert got["total"] == 5000 and len(got["items"]) == 200


def test_new_today_lists_open_items_first_seen_today_in_hcmc(api_at):  # M3: the "New today" card opens this list
    api = api_at(datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc))  # 00:30 on the 28th in HCMC
    seed(api, "New open", first_seen_at="2026-10-27 17:10:00")
    seed(api, "New dismissed", status="dismissed", first_seen_at="2026-10-27 17:10:00")
    seed(api, "Yesterday", first_seen_at="2026-10-27 16:50:00")
    assert titles(api, tab="all", new_today="true") == ["New open"]
    assert api.get("/api/today")["counts"]["new_today"] == 1
