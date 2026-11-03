"""Mailbox: the only code that touches IMAP, and it can only read.

Run: pytest
"""

import imaplib

import pytest
from fake_imap import ALL_MAIL, FakeImap, FakeMessage

from assistant import gmail_imap
from assistant.config import load_config
from assistant.errors import AssistantError, ConfigError, GmailAuthError
from assistant.gmail_imap import Bookmark, GmailBusy, MailRef, MessageGone, open_mailbox, token_fingerprint
from assistant.vault import save

PASSWORD = "abcdefghijklmnop"
ID_1 = 1760000000000
HEX_1 = format(ID_1, "x")


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "config.toml").write_text('[gmail]\naddress = "me@gmail.com"\n', encoding="utf-8")
    return load_config(tmp_path / "config.toml")


@pytest.fixture
def fake(monkeypatch):
    server = FakeImap(messages=[FakeMessage(uid=5, msgid=ID_1), FakeMessage(uid=6, msgid=ID_1 + 1, labels="Work"),
                                FakeMessage(uid=9, msgid=ID_1 + 2, labels='"\\\\Sent" "My label"')])
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", server)
    save("gmail", PASSWORD)
    return server


def test_it_signs_in_and_opens_all_mail_read_only(cfg, fake):
    box = open_mailbox(cfg)
    assert fake.connected_with == ("imap.gmail.com", 993, 30)
    assert fake.credentials == ("me@gmail.com", PASSWORD)
    assert fake.commands[:3] == ["LOGIN", "LIST", f"EXAMINE {ALL_MAIL}"]
    assert box.address == "me@gmail.com"


def test_all_mail_is_found_by_its_attribute_in_any_language(cfg, fake):
    name = b'"[Gmail]/T&AOU-t c&HqM- th&AbA-"'
    fake.folders = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\All) "/" ' + name]
    open_mailbox(cfg)
    assert fake.opened == name.decode()


def test_all_mail_hidden_from_imap_says_what_to_change(cfg, fake):
    fake.folders = [b'(\\HasNoChildren) "/" "INBOX"']
    with pytest.raises(AssistantError, match="Show in IMAP"):
        open_mailbox(cfg)
    assert fake.logged_out


def test_a_rejected_password_is_an_auth_error_without_the_password(cfg, fake):
    fake.refuse_login = True
    with pytest.raises(GmailAuthError, match="set-key gmail") as info:
        open_mailbox(cfg)
    assert PASSWORD not in str(info.value) and info.value.__cause__ is None


def test_a_missing_address_says_what_to_set(tmp_path, fake):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(GmailAuthError, match=r"\[gmail\] address"):
        open_mailbox(load_config(tmp_path / "config.toml"))
    assert fake.commands == []


def test_a_missing_password_says_what_to_set(cfg, monkeypatch):
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", FakeImap())
    with pytest.raises(GmailAuthError, match="set-key gmail"):
        open_mailbox(cfg)


def test_the_bookmark_comes_from_the_folder(cfg, fake):
    fake.uidnext = b"42"
    assert open_mailbox(cfg).bookmark() == Bookmark("7", 41)


def test_list_new_returns_hex_ids_and_labels(cfg, fake):
    refs = open_mailbox(cfg).list_new(4)
    assert refs == [MailRef(HEX_1, 5, ("INBOX",)), MailRef(format(ID_1 + 1, "x"), 6, ("Work",)),
                    MailRef(format(ID_1 + 2, "x"), 9, ("SENT", "My label"))]


@pytest.mark.parametrize("gmail_wrote, kept", [
    ('"\\\\Inbox" "\\\\Important"', ("INBOX", "IMPORTANT")), ("\\Inbox Important", ("INBOX", "IMPORTANT")),
    ('"\\\\Draft"', ("DRAFT",)), ('"\\\\Drafts"', ("DRAFT",)), ('"\\\\Starred"', ("STARRED",)),
    ('"Has (parens) and \\"quotes\\""', ('Has (parens) and "quotes"',)), ("", ()),
])
def test_labels_are_mapped_however_gmail_writes_them(cfg, fake, gmail_wrote, kept):
    fake.messages = [FakeMessage(uid=5, msgid=ID_1, labels=gmail_wrote)]
    assert open_mailbox(cfg).list_new(0)[0].labels == kept


def test_a_quiet_mailbox_lists_nothing_new(cfg, fake):
    assert open_mailbox(cfg).list_new(9) == []  # the server answers with UID 9 anyway
    assert not any(c.startswith("UID FETCH") for c in fake.commands)


def test_categories_are_added_from_a_search(cfg, fake):
    fake.categories = {"promotions": [5], "primary": [6]}
    labels = {r.uid: r.labels for r in open_mailbox(cfg).list_new(0)}
    assert labels[5] == ("INBOX", "CATEGORY_PROMOTIONS")
    assert labels[6] == ("Work", "CATEGORY_PERSONAL")


def test_large_lists_go_in_batches_of_500(cfg, fake):
    fake.messages = [FakeMessage(uid=n, msgid=ID_1 + n) for n in range(1, 1202)]
    assert len(open_mailbox(cfg).list_new(0)) == 1201
    fetches = [c for c in fake.commands if c.startswith("UID FETCH")]
    assert len(fetches) == 3 and all(c.count(",") < 500 for c in fetches)


def test_search_passes_the_query_and_keeps_the_newest(cfg, fake):
    refs = open_mailbox(cfg).search('newer_than:3d -in:spam subject:"a b"', limit=2)
    assert [r.uid for r in refs] == [6, 9]
    assert 'UID SEARCH X-GM-RAW "newer_than:3d -in:spam subject:\\"a b\\""' in fake.commands


def test_search_with_no_hits_fetches_nothing(cfg, fake):
    fake.search_hits = []
    assert open_mailbox(cfg).search("newer_than:1d", limit=5) == []
    assert not any(c.startswith("UID FETCH") for c in fake.commands)


@pytest.mark.parametrize("query", ["có dấu", "line\r\nA1 DELETE x"])
def test_a_query_that_isnt_plain_text_is_refused(cfg, fake, query):
    with pytest.raises(ConfigError, match="query"):
        open_mailbox(cfg).search(query, limit=5)
    assert not any("X-GM-RAW" in c for c in fake.commands)


def test_get_message_downloads_with_peek_and_converts(cfg, fake):
    box = open_mailbox(cfg)
    box.list_new(0)
    message = box.get_message(HEX_1)
    assert (message["id"], message["threadId"], message["labelIds"], message["internalDate"]) == (
        HEX_1, format(99, "x"), ["INBOX"], "1760000000000")
    assert "UID FETCH 5 (X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE BODY.PEEK[]<0.2000000>)" in fake.commands
    assert message["snippet"].startswith("The body text")


def test_a_message_gone_before_fetching_raises_message_gone(cfg, fake):
    box = open_mailbox(cfg)
    box.list_new(0)
    fake.messages = fake.messages[1:]
    with pytest.raises(MessageGone):
        box.get_message(HEX_1)
    with pytest.raises(MessageGone):
        box.get_message("never-listed")


def test_nothing_outside_the_allow_list_reaches_the_server(cfg, fake):
    box = open_mailbox(cfg)
    before = list(fake.commands)
    for command in ("STORE", "EXPUNGE", "COPY", "APPEND", "DELETE", "CREATE", "RENAME", "STATUS", "SELECT",
                    "UID STORE", "UID COPY", "UID MOVE", "UID EXPUNGE"):
        with pytest.raises(PermissionError):
            box._send(command, "1")
    for items in ("(BODY[])", "(RFC822)", "(BODY[TEXT])", "(X-GM-MSGID BODY[])", "(FLAGS) +FLAGS"):
        with pytest.raises(PermissionError):
            box._send("UID FETCH", "1", items)
    assert fake.commands == before


@pytest.mark.parametrize("text, raised", [
    ("[OVERQUOTA] Account exceeded command or bandwidth limits", GmailBusy), ("[THROTTLED] slow down", GmailBusy),
    ("Too many simultaneous connections", GmailBusy), ("[UNAVAILABLE] Temporary System Error", ConnectionError)])
def test_gmail_trouble_is_translated(cfg, fake, text, raised):
    box = open_mailbox(cfg)
    fake.fail_with = imaplib.IMAP4.error(text)
    with pytest.raises(raised):
        box.list_new(0)


def test_a_dropped_connection_stays_what_it_is(cfg, fake):
    box = open_mailbox(cfg)
    fake.fail_with = imaplib.IMAP4.abort("socket error: EOF")
    with pytest.raises(imaplib.IMAP4.abort):
        box.list_new(0)
    fake.fail_with = OSError("network down")
    with pytest.raises(OSError):
        box.list_new(0)


def test_any_other_imap_error_stays_what_it_is(cfg, fake):
    box = open_mailbox(cfg)
    fake.fail_with = imaplib.IMAP4.error("something odd")
    with pytest.raises(imaplib.IMAP4.error):
        box.list_new(0)


def test_it_works_as_a_context_manager_and_logs_out(cfg, fake):
    with open_mailbox(cfg) as box:
        box.list_new(0)
    assert fake.commands[-1] == "LOGOUT"


def test_close_survives_a_dead_connection(cfg, fake, monkeypatch):
    box = open_mailbox(cfg)

    def dead():
        raise OSError("gone")

    monkeypatch.setattr(fake, "logout", dead)
    box.close()  # no exception


def test_the_fingerprint_changes_with_the_saved_password(fake):
    first = token_fingerprint()
    assert first is not None and PASSWORD.encode() not in first
    save("gmail", "qrstuvwxyzabcdef")
    assert token_fingerprint() not in (None, first)


def test_no_saved_password_has_no_fingerprint():
    assert token_fingerprint() is None
