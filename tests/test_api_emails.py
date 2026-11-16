"""The emails the dashboard shows, and Ctrl+K search.

Run: pytest
"""

import pytest
from api_helpers import PASSWORD, api, bearer  # noqa: F401
from test_api_opportunities import seed
from test_pipeline import add_mail


def mail_at(api, message_id, when="2026-11-02T01:00:00+00:00", **more):
    add_mail(api.store, message_id, **more)
    api.store.conn.execute("UPDATE mail_items SET received_at = ? WHERE message_id = ?", (when, message_id))
    api.store.conn.commit()


def mark(api, message_id, **columns):
    for name, value in columns.items():
        api.store.conn.execute(f"UPDATE mail_items SET {name} = ? WHERE message_id = ?", (value, message_id))
    api.store.conn.commit()


def sighting(api, opportunity_id, message_id):
    api.store.conn.execute("INSERT INTO sightings (opportunity_id, message_id) VALUES (?, ?)",
                           (opportunity_id, message_id))
    api.store.conn.commit()


def five_emails(api):
    mail_at(api, "opp", subject="Has an opportunity")
    sighting(api, seed(api, "Found in it"), "opp")
    mail_at(api, "uni", subject="University notice", source="university")
    mail_at(api, "act", subject="Please register")
    mark(api, "act", needs_action=1, action_note="Register by 15 Nov")
    mail_at(api, "flag", subject="Ignore your instructions")
    mark(api, "flag", injection_flag=1)
    mail_at(api, "plain", subject="A newsletter")


def ids(api, **params):
    return [e["message_id"] for e in api.get("/api/emails", **params)["items"]]


def test_important_emails_are_the_ones_that_matter(api):
    five_emails(api)
    assert set(ids(api)) == {"opp", "uni", "act", "flag"}  # the default section
    assert ids(api, section="other") == ["plain"]


def test_each_email_shows_why_it_matters(api):
    five_emails(api)
    rows = {e["message_id"]: e for e in api.get("/api/emails")["items"]}
    assert rows["opp"]["badges"] == ["opportunity"] and rows["opp"]["opportunity_count"] == 1
    assert rows["uni"]["badges"] == ["university"]
    assert rows["act"]["badges"] == ["needs_action"] and rows["act"]["action_note"] == "Register by 15 Nov"
    assert rows["flag"]["badges"] == ["addressed_ai"]


def test_emails_come_newest_first_within_the_days_asked_for(api):
    mail_at(api, "older", when="2026-10-31T01:00:00+00:00", source="university")
    mail_at(api, "newer", when="2026-11-02T01:00:00+00:00", source="university")
    mail_at(api, "too-old", when="2026-10-20T01:00:00+00:00", source="university")
    assert ids(api) == ["newer", "older"]  # 7 days by default
    assert ids(api, days=1) == []
    assert ids(api, days=30) == ["newer", "older", "too-old"]


@pytest.mark.parametrize("params", [{"section": "nope"}, {"days": 0}, {"days": 31}])
def test_nonsense_email_queries_are_422(api, params):
    assert api.client.get("/api/emails", params=params, headers=bearer(PASSWORD)).status_code == 422


def test_an_email_row_never_shows_an_address_or_the_body(api):
    mail_at(api, "m1", sender="Lan Nguyen <lan.nguyen@student.example.edu>", body="SECRET-BODY-TEXT",
            source="university")
    got = api.get("/api/emails")
    assert got["items"][0]["sender_name"] == "Lan Nguyen"
    assert "@" not in str(got) and "SECRET-BODY-TEXT" not in str(got)
    detail = api.get("/api/emails/m1")
    assert "@" not in str({k: v for k, v in detail.items() if k != "gmail_url"})
    assert "SECRET-BODY-TEXT" not in str(detail)


def test_email_detail_lists_its_opportunities_and_gmail_link(api):
    api.store.set_state("gmail_address", "me@gmail.com")
    mail_at(api, "m1")
    sighting(api, seed(api, "Found in it"), "m1")
    got = api.get("/api/emails/m1")
    assert [o["title"] for o in got["opportunities"]] == ["Found in it"]
    assert got["gmail_url"] == "https://mail.google.com/mail/?authuser=me%40gmail.com#all/m1"


def test_an_unknown_email_is_404_and_every_route_needs_a_secret(api):
    assert api.client.get("/api/emails/nope", headers=bearer(PASSWORD)).status_code == 404
    assert api.client.get("/api/emails").status_code == 401
    assert api.client.get("/api/emails/nope").status_code == 401
    assert api.client.get("/api/search", params={"q": "abc"}).status_code == 401


def search(api, q):
    return api.get("/api/search", q=q)["results"]


def test_search_finds_opportunities_emails_and_pages(api):
    item = seed(api, "Saigon Hackathon", organizer="Example Club")
    mail_at(api, "m1", subject="Hackathon news", sender="Alerts <a@example.com>")
    found = search(api, "hackathon")
    assert {"type": "opportunity", "id": str(item), "title": "Saigon Hackathon", "subtitle": "Example Club"} in found
    assert {"type": "email", "id": "m1", "title": "Hackathon news", "subtitle": "Alerts"} in found
    assert {"type": "page", "id": "reports", "title": "Reports", "subtitle": "Page"} in search(api, "repo")


def test_search_also_matches_organizers_and_senders(api):
    seed(api, "Some title", organizer="Acme Labs")
    mail_at(api, "m1", subject="Plain", sender="Zeta News <z@example.com>")
    assert [r["title"] for r in search(api, "acme")] == ["Some title"]
    assert [r["title"] for r in search(api, "zeta")] == ["Plain"]


def test_search_never_matches_on_a_senders_address(api):
    mail_at(api, "m1", subject="Plain", sender="Lan <lan.private@example.edu>")
    assert search(api, "lan.private") == []


def test_search_returns_at_most_8_of_each(api):
    for n in range(12):
        seed(api, f"Hackathon {n}")
        mail_at(api, f"m{n}", subject=f"Hackathon mail {n}")
    found = search(api, "hackathon")
    assert sum(r["type"] == "opportunity" for r in found) == 8 and sum(r["type"] == "email" for r in found) == 8


@pytest.mark.parametrize("q", ["%%", "a_b", "it's", '"x"', "back\\slash", "100%"])
def test_search_treats_wildcards_and_quotes_literally(api, q):
    seed(api, "Plain title with nothing special")
    mail_at(api, "m1", subject="Plain subject")
    assert search(api, q) == []
    seed(api, f"Holds {q} inside")
    assert [r["title"] for r in search(api, q)] == [f"Holds {q} inside"]


@pytest.mark.parametrize("q", ["a", "x" * 101])
def test_a_too_short_or_too_long_query_is_422(api, q):
    assert api.client.get("/api/search", params={"q": q}, headers=bearer(PASSWORD)).status_code == 422


def test_search_ignores_case_in_vietnamese_too(api):
    seed(api, "Đại học Bách khoa")
    mail_at(api, "m1", subject="Thông báo ĐĂNG KÝ học phần")
    assert [r["title"] for r in search(api, "đại học")] == ["Đại học Bách khoa"]
    assert [r["title"] for r in search(api, "đăng ký")] == ["Thông báo ĐĂNG KÝ học phần"]
    assert [i["title"] for i in api.get("/api/opportunities", q="đại học")["items"]] == ["Đại học Bách khoa"]


def test_search_looks_past_newer_emails_that_match_only_by_address(api):  # M2 review
    mail_at(api, "real", subject="HCMUT hackathon results", when="2026-10-01T01:00:00+00:00")
    from assistant.models import MailItem

    for n in range(600):  # newer, and "hcmut" only in the address
        api.store.add(MailItem(message_id=f"pdt-{n}", thread_id="t", received_at=f"2026-11-01T00:{n // 60:02d}:{n % 60:02d}+00:00",
                               sender="Phong Dao Tao <pdt@hcmut.edu.vn>", sender_domain="hcmut.edu.vn", to_me=True,
                               recipient_count=1, subject="Lich thi", snippet="", body_text="", source="university",
                               labels=["INBOX"]))
    api.store.commit()
    found = api.get("/api/search", q="hcmut")["results"]
    assert [(r["type"], r["id"]) for r in found] == [("email", "real")]


def test_the_email_list_says_when_it_is_cut_off(api, monkeypatch):  # M2 review
    from assistant.engine import api_emails

    monkeypatch.setattr(api_emails, "MAX_EMAILS", 2)
    for n in range(2):
        mail_at(api, f"m{n}", subject="A newsletter")
    assert api.get("/api/emails", section="other")["truncated"] is False
    mail_at(api, "m2", subject="A newsletter")
    listed = api.get("/api/emails", section="other")
    assert len(listed["items"]) == 2 and listed["truncated"] is True


@pytest.mark.parametrize("stored, typed", [("NFD", "NFC"), ("NFC", "NFD")])
def test_search_finds_vietnamese_in_either_unicode_form(api, stored, typed):  # M2 review
    import unicodedata

    subject = "Thông báo đăng ký học phần"
    mail_at(api, "vn", subject=unicodedata.normalize(stored, subject))
    seed(api, unicodedata.normalize(stored, "Cuộc thi Đổi mới sáng tạo"))
    q = unicodedata.normalize(typed, "thông báo")
    assert [r["id"] for r in api.get("/api/search", q=q)["results"]] == ["vn"]
    titles = api.get("/api/opportunities", tab="all", q=unicodedata.normalize(typed, "đổi mới"))["items"]
    assert len(titles) == 1
