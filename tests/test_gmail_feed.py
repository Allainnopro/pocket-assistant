"""The engine's mail check: new UIDs in All Mail, and the search it falls back to.

Run: pytest
"""

from datetime import datetime, timedelta, timezone

import pytest
from fake_mailbox import FakeMailbox
from test_parser import make_message

from assistant.config import Config
from assistant.engine.gmail_feed import CheckResult, check_mail
from assistant.ingest import query_exclusions
from assistant.store import Store

NOW = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)
QUERY = "newer_than:{days}d -in:chats -in:spam -in:trash"


def message(msg_id, labels=("INBOX",)):
    found = make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id=msg_id)
    found["labelIds"] = list(labels)
    return found


@pytest.fixture
def cfg(tmp_path):
    return Config(QUERY, 50, (), tmp_path / "a.db", 4000)


def bookmarked(cfg, last_uid=1, uidvalidity="1", address="me@x.com"):
    store = Store(cfg.db_path)
    store.set_state("imap_uidvalidity", uidvalidity)
    store.set_state("imap_last_uid", str(last_uid))
    store.set_state("gmail_address", address)
    return store


def test_check_mail_uses_the_feed_when_it_has_a_bookmark(cfg):
    store = bookmarked(cfg)
    store.set_state("last_check_error", "offline")
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=2, mode="feed")
    assert "search" not in fake.calls and store.get_state("imap_last_uid") == "3"
    assert not store.has("a") and store.has("b") and store.has("c")  # uid 1 is at the bookmark
    assert store.get_state("last_ok_check_at") == NOW.isoformat() and store.get_state("last_check_error") is None
    assert store.get_state("gmail_address") == "me@x.com"


def test_a_quiet_check_changes_nothing_but_the_time(cfg):
    store = bookmarked(cfg, last_uid=2)
    fake = FakeMailbox([message("a"), message("b")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=0, mode="feed")
    assert store.get_state("imap_last_uid") == "2" and "get_message" not in fake.calls


def test_a_first_check_without_a_bookmark_searches_7_days(cfg):
    store = Store(cfg.db_path)
    fake = FakeMailbox([message("a")], uidvalidity="9")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert fake.queries == ["newer_than:7d -in:chats -in:spam -in:trash -in:sent -in:drafts"]
    assert (store.get_state("imap_uidvalidity"), store.get_state("imap_last_uid")) == ("9", "1")


def test_the_fallback_takes_its_bookmark_before_searching(cfg):
    store = Store(cfg.db_path)
    store.set_state("last_ok_check_at", (NOW - timedelta(days=3)).isoformat())
    fake = FakeMailbox([message("a")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert fake.queries == ["newer_than:3d -in:chats -in:spam -in:trash -in:sent -in:drafts"]
    assert fake.calls.index("bookmark") < fake.calls.index("search")


def test_mail_arriving_during_the_search_is_caught_by_the_next_check(cfg):
    store = Store(cfg.db_path)

    class Arrives(FakeMailbox):
        def search(self, query, limit):
            found = super().search(query, limit)
            self.add(message("late"))  # after the search looked, before the bookmark is saved
            return found

    fake = Arrives([message("a")])
    check_mail(store, fake, cfg, now_utc=NOW)
    assert not store.has("late")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert store.has("late")


def test_a_changed_uidvalidity_searches_instead(cfg):
    store = bookmarked(cfg, last_uid=50, uidvalidity="1")
    fake = FakeMailbox([message("a")], uidvalidity="2")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert (store.get_state("imap_uidvalidity"), store.get_state("imap_last_uid")) == ("2", "1")


def test_a_message_deleted_before_fetch_is_skipped_and_the_bookmark_still_advances(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("a"), message("gone")])
    fake.delete("gone")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert store.get_state("imap_last_uid") == "2" and not store.has("gone")


def test_a_naive_time_is_refused(cfg):
    with pytest.raises(ValueError):
        check_mail(Store(cfg.db_path), FakeMailbox(), cfg, now_utc=datetime(2026, 11, 3, 3))


def test_skipped_labels_move_the_bookmark_but_download_nothing(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("mine", labels=("SENT",)), message("draft", labels=("DRAFT",)), message("a")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert fake.calls.count("get_message") == 1 and store.get_state("imap_last_uid") == "3"
    assert not store.has("mine")


def test_a_skipped_email_at_the_end_still_moves_the_bookmark(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("a"), message("mine", labels=("SENT",))])
    check_mail(store, fake, cfg, now_utc=NOW)
    assert store.get_state("imap_last_uid") == "2"


def test_a_rescued_email_arrives_as_a_new_uid(cfg):
    store = bookmarked(cfg, last_uid=1)
    fake = FakeMailbox([message("a")])
    fake.add(message("rescued"))  # out of Spam or the bin: a new, higher UID in All Mail
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert store.has("rescued")


def test_the_fallback_search_is_not_cut_off_at_the_cli_limit(tmp_path):
    cfg = Config(QUERY, 2, (), tmp_path / "a.db", 4000)
    fake = FakeMailbox([message(i) for i in "abc"])
    # the bookmark moves past whatever the search leaves out, so it must leave nothing out
    assert check_mail(Store(cfg.db_path), fake, cfg, now_utc=NOW) == CheckResult(new=3, mode="search")


def test_the_feed_honours_label_exclusions_in_the_configured_query(tmp_path):
    cfg = Config(QUERY + " -category:promotions", 50, (), tmp_path / "a.db", 4000)
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("promo", labels=("INBOX", "CATEGORY_PROMOTIONS")), message("b")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert store.has("b") and not store.has("promo") and fake.calls.count("get_message") == 1


@pytest.mark.parametrize("query, labels, unsupported", [
    ("newer_than:{days}d -in:chats -in:spam -in:trash", {"CHAT", "SPAM", "TRASH"}, []),
    ("newer_than:{days}d -category:promotions -category:social -in:sent from:boss@example.com",
     {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SENT"}, ["from:boss@example.com"]),
    ("newer_than:{days}d -label:work (a OR b)", set(), ["-label:work", "(a", "OR", "b)"]),
])
def test_label_exclusions_are_read_from_the_query(query, labels, unsupported):
    assert query_exclusions(query) == (frozenset(labels), unsupported)


def test_a_connection_lost_midway_keeps_the_saved_mail_and_the_bookmark(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    fake.get_failures["b"] = OSError("the lid closed")
    with pytest.raises(OSError):
        check_mail(store, fake, cfg, now_utc=NOW)
    assert store.has("a") and not store.has("b")
    assert store.get_state("imap_last_uid") == "0" and store.get_state("last_ok_check_at") is None
    fake.get_failures.clear()
    fake.calls.clear()
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=2, mode="feed")
    assert fake.calls.count("get_message") == 2  # "a" isn't fetched again
    assert store.get_state("imap_last_uid") == "3"


def test_a_search_lost_midway_saves_no_bookmark_so_the_next_check_searches_again(cfg):
    store = Store(cfg.db_path)
    fake = FakeMailbox([message("a"), message("b")])
    fake.get_failures["b"] = OSError("the lid closed")
    with pytest.raises(OSError):
        check_mail(store, fake, cfg, now_utc=NOW)
    assert store.get_state("imap_last_uid") is None and store.get_state("imap_uidvalidity") is None
    fake.get_failures.clear()
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert store.has("b")


def test_stored_emails_are_not_downloaded_again(cfg):
    store = Store(cfg.db_path)
    fake = FakeMailbox([message("a"), message("b")])
    check_mail(store, fake, cfg, now_utc=NOW)
    store.set_state("imap_uidvalidity", None)  # the next check must search over the same mail
    fake.calls.clear()
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=0, mode="search")
    assert "get_message" not in fake.calls


def test_the_old_history_bookmark_is_cleaned_up(cfg):
    store = Store(cfg.db_path)
    store.set_state("gmail_history_id", "100")
    check_mail(store, FakeMailbox(), cfg, now_utc=NOW)
    assert store.get_state("gmail_history_id") is None


def test_an_empty_mailbox_gets_a_bookmark_that_finds_the_first_email(cfg):
    store = Store(cfg.db_path)
    fake = FakeMailbox()
    check_mail(store, fake, cfg, now_utc=NOW)
    assert store.get_state("imap_last_uid") == "0"
    fake.add(message("first"))
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")


# --- review fixes: broken and refused mail, the account, the order of the bookmark -----------------

def test_an_unreadable_email_is_skipped_and_the_bookmark_moves(cfg):
    from assistant.gmail_imap import UnreadableMail

    store, lines = bookmarked(cfg, last_uid=0), []
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    fake.get_failures["b"] = UnreadableMail("b", "IndexError")
    assert check_mail(store, fake, cfg, now_utc=NOW, log=lines.append) == CheckResult(new=2, mode="feed")
    assert store.get_state("imap_last_uid") == "3" and not store.has("b")
    assert any("b" in line and "IndexError" in line for line in lines)


def test_an_unreadable_email_doesnt_stop_the_fallback(cfg):
    from assistant.gmail_imap import UnreadableMail

    store = Store(cfg.db_path)
    fake = FakeMailbox([message("a"), message("b")])
    fake.get_failures["a"] = UnreadableMail("a", "RecursionError")
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert store.get_state("imap_last_uid") == "2"


def refused(fake, msg_id):
    from assistant.gmail_imap import MessageRefused

    fake.get_failures[msg_id] = MessageRefused(msg_id)


def test_a_refused_email_holds_the_bookmark_just_below_it_then_gives_up(cfg):
    store, lines = bookmarked(cfg, last_uid=0), []
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    refused(fake, "b")
    assert check_mail(store, fake, cfg, now_utc=NOW, log=lines.append) == CheckResult(new=2, mode="feed")
    assert store.get_state("imap_last_uid") == "1" and store.has("c")  # the rest still comes in
    check_mail(store, fake, cfg, now_utc=NOW, log=lines.append)
    assert store.get_state("imap_last_uid") == "1"
    check_mail(store, fake, cfg, now_utc=NOW, log=lines.append)  # the third refusal in a row
    assert store.get_state("imap_last_uid") == "3" and store.get_state("imap_refused") is None
    assert any("gave up" in line and "b" in line for line in lines)


def test_a_refusal_that_clears_brings_the_email_in(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    refused(fake, "b")
    check_mail(store, fake, cfg, now_utc=NOW)
    fake.get_failures.clear()
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="feed")
    assert store.has("b") and store.get_state("imap_last_uid") == "3" and store.get_state("imap_refused") is None


def test_a_different_refused_email_starts_its_own_count(cfg):
    store = bookmarked(cfg, last_uid=0)
    fake = FakeMailbox([message("a"), message("b"), message("c")])
    refused(fake, "b")
    check_mail(store, fake, cfg, now_utc=NOW)
    check_mail(store, fake, cfg, now_utc=NOW)
    fake.get_failures.clear()  # b gets through; a newly arrived d is refused
    fake.add(message("d"))
    refused(fake, "d")
    check_mail(store, fake, cfg, now_utc=NOW)
    assert store.has("b") and store.get_state("imap_last_uid") == "3" and store.get_state("imap_refused") == "4:1"


def test_a_refused_email_in_the_fallback_is_skipped_with_a_note(cfg):
    store, lines = Store(cfg.db_path), []
    fake = FakeMailbox([message("a"), message("b")])
    refused(fake, "a")
    assert check_mail(store, fake, cfg, now_utc=NOW, log=lines.append) == CheckResult(new=1, mode="search")
    assert store.get_state("imap_last_uid") == "2" and any("a" in line for line in lines)


def test_a_bookmark_from_another_account_is_not_used(cfg):
    store = bookmarked(cfg, last_uid=50, address="old@x.com")
    fake = FakeMailbox([message("a")])
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=1, mode="search")
    assert store.get_state("gmail_address") == "me@x.com"


def test_the_bookmark_number_is_saved_before_its_uidvalidity(cfg):
    """A crash between the two writes must leave a bookmark that searches again, never
    a new UIDVALIDITY paired with the old, higher number."""
    store = bookmarked(cfg, last_uid=50, uidvalidity="1")
    fake = FakeMailbox([message("a")], uidvalidity="2")
    real, written = store.set_state, []

    def crash_on_the_second_write(key, value):
        if key in ("imap_last_uid", "imap_uidvalidity"):
            if written:
                raise RuntimeError("the process died here")
            written.append(key)
        real(key, value)

    store.set_state = crash_on_the_second_write
    with pytest.raises(RuntimeError):
        check_mail(store, fake, cfg, now_utc=NOW)
    store.set_state = real
    fake.add(message("late"))
    assert check_mail(store, fake, cfg, now_utc=NOW).mode == "search"
    assert store.has("late")

