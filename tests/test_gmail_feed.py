"""The engine's mail check: Gmail's change feed, and the search it falls back to.

Run: pytest
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fake_gmail import FakeGmail
from test_parser import make_message

from assistant.config import Config
from assistant.engine.gmail_feed import CheckResult, HistoryExpired, check_mail, fetch_new_ids
from assistant.ingest import query_exclusions
from assistant.store import Store

NOW = datetime(2026, 10, 25, 3, tzinfo=timezone.utc)


def message(msg_id, labels=("INBOX",)):
    found = make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id=msg_id)
    found["labelIds"] = list(labels)
    return found


def added(*ids, labels=("INBOX",)):
    return {"id": "101", "messagesAdded": [{"message": {"id": i, "threadId": "t", "labelIds": list(labels)}}
                                           for i in ids]}


def two_pages():
    return [{"history": [added("a", "b"), added("s", labels=("SPAM",))], "nextPageToken": "1", "historyId": "120"},
            {"history": [added("b", "c")], "historyId": "130"}]


@pytest.fixture
def cfg(tmp_path):
    return Config(Path("unused.json"), "newer_than:{days}d -in:chats -in:spam -in:trash", 50, (),
                  tmp_path / "a.db", 4000)


def bookmarked(cfg, history_id="100"):
    store = Store(cfg.db_path)
    store.set_state("gmail_history_id", history_id)
    store.set_state("gmail_address", "me@x.com")
    return store


def test_the_feed_returns_added_ids_in_order_without_duplicates_and_the_new_bookmark():
    fake = FakeGmail([], profile="me@x.com")
    fake.history_pages = two_pages()
    assert fetch_new_ids(fake, "100") == (["a", "b", "c"], "130")
    first = fake.history_requests[0]
    assert (first["startHistoryId"], first["historyTypes"], first["maxResults"]) == (
        "100", ["messageAdded", "labelRemoved"], 500)
    assert [r["pageToken"] for r in fake.history_requests] == [None, "1"]


def test_an_expired_bookmark_raises_history_expired():
    fake = FakeGmail([], profile="me@x.com")
    fake.expire_history()
    with pytest.raises(HistoryExpired):
        fetch_new_ids(fake, "100")


def test_check_mail_uses_the_feed_when_it_has_a_bookmark(cfg):
    store = bookmarked(cfg)
    store.set_state("last_check_error", "offline")
    fake = FakeGmail([message("a"), message("b"), message("c")], profile="me@x.com")
    fake.history_pages = [{"history": [added("a", "b")], "historyId": "130"}]
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=2, mode="history")
    assert "messages.list" not in fake.calls and store.get_state("gmail_history_id") == "130"
    assert store.has("a") and store.has("b") and not store.has("c")
    assert store.get_state("last_ok_check_at") == NOW.isoformat() and store.get_state("last_check_error") is None


def test_the_fallback_takes_its_bookmark_before_searching(cfg):
    store = bookmarked(cfg)
    store.set_state("last_ok_check_at", (NOW - timedelta(days=3)).isoformat())
    fake = FakeGmail([message("a")], profile="Me@X.com", history_id="200")
    fake.expire_history()
    result = check_mail(store, fake, cfg, now_utc=NOW)
    assert result == CheckResult(new=1, mode="search")
    assert fake.queries == ["newer_than:3d -in:chats -in:spam -in:trash -in:sent -in:drafts"]
    assert fake.calls.index("getProfile") < fake.calls.index("messages.list")
    assert (store.get_state("gmail_history_id"), store.get_state("gmail_address")) == ("200", "me@x.com")


def test_a_first_check_without_a_bookmark_searches_7_days(cfg):
    store = Store(cfg.db_path)
    fake = FakeGmail([message("a")], profile="me@x.com", history_id="200")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert fake.queries == ["newer_than:7d -in:chats -in:spam -in:trash -in:sent -in:drafts"]
    assert "history.list" not in fake.calls and store.get_state("gmail_history_id") == "200"


def test_a_message_deleted_before_fetch_is_skipped_and_the_bookmark_still_advances(cfg):
    store = bookmarked(cfg)
    fake = FakeGmail([message("a"), message("gone")], profile="me@x.com")
    fake.history_pages = [{"history": [added("a", "gone")], "historyId": "130"}]
    fake.delete("gone")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="history")
    assert store.get_state("gmail_history_id") == "130" and not store.has("gone")


def test_a_naive_time_is_refused(cfg):
    with pytest.raises(ValueError):
        check_mail(Store(cfg.db_path), FakeGmail([], profile="me@x.com"), cfg, now_utc=datetime(2026, 10, 25, 3))


def removed(msg_id, labels):
    return {"id": "105", "labelsRemoved": [{"message": {"id": msg_id, "threadId": "t", "labelIds": ["INBOX"]},
                                            "labelIds": list(labels)}]}


def test_mail_rescued_from_spam_or_trash_is_picked_up(cfg):
    store = bookmarked(cfg)
    fake = FakeGmail([message("x"), message("y"), message("read")], profile="me@x.com")
    fake.history_pages = [{"history": [
        removed("x", ["SPAM"]),      # "Not spam"
        removed("y", ["TRASH"]),     # restored from the bin
        removed("read", ["UNREAD"]),  # only opened: not new mail
    ], "historyId": "130"}]
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=2, mode="history")
    assert store.has("x") and store.has("y") and not store.has("read")
    assert fake.calls.count("messages.get") == 2


def test_the_fallback_search_is_not_cut_off_at_the_cli_limit(tmp_path):
    cfg = Config(Path("unused.json"), "newer_than:{days}d -in:chats -in:spam -in:trash", 2, (),
                 tmp_path / "a.db", 4000)
    fake = FakeGmail([message(i) for i in "abc"], profile="me@x.com", history_id="200")
    # the bookmark moves past whatever the search leaves out, so it must leave nothing out
    assert check_mail(Store(cfg.db_path), fake, cfg, now_utc=NOW) == CheckResult(new=3, mode="search")


def test_the_feed_honours_label_exclusions_in_the_configured_query(tmp_path):
    cfg = Config(Path("unused.json"), "newer_than:{days}d -in:chats -in:spam -in:trash -category:promotions", 50,
                 (), tmp_path / "a.db", 4000)
    store = bookmarked(cfg)
    fake = FakeGmail([message("promo", labels=("INBOX", "CATEGORY_PROMOTIONS")), message("b")], profile="me@x.com")
    fake.history_pages = [{"history": [added("promo", labels=("INBOX", "CATEGORY_PROMOTIONS")), added("b")],
                           "historyId": "130"}]
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="history")
    assert store.has("b") and not store.has("promo")


@pytest.mark.parametrize("query, labels, unsupported", [
    ("newer_than:{days}d -in:chats -in:spam -in:trash", {"CHAT", "SPAM", "TRASH"}, []),
    ("newer_than:{days}d -category:promotions -category:social -in:sent from:boss@example.com",
     {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SENT"}, ["from:boss@example.com"]),
    ("newer_than:{days}d -label:work (a OR b)", set(), ["-label:work", "(a", "OR", "b)"]),
])
def test_label_exclusions_are_read_from_the_query(query, labels, unsupported):
    assert query_exclusions(query) == (frozenset(labels), unsupported)


def test_rescued_mail_still_honours_the_query_exclusions(tmp_path):
    cfg = Config(Path("unused.json"), "newer_than:{days}d -in:chats -in:spam -in:trash -category:promotions", 50,
                 (), tmp_path / "a.db", 4000)
    store = bookmarked(cfg)
    fake = FakeGmail([message("promo", labels=("INBOX", "CATEGORY_PROMOTIONS"))], profile="me@x.com")
    fake.history_pages = [{"history": [removed("promo", ["SPAM"])], "historyId": "130"}]
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=0, mode="history")
    assert not store.has("promo")
