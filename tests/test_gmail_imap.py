"""Mailbox: the only code that touches IMAP, and it can only read.

Run: pytest
"""

import imaplib
from pathlib import Path

import pytest
from fake_imap import ALL_MAIL, FakeImap, FakeMessage

from assistant import gmail_imap
from assistant.config import load_config
from assistant.errors import AssistantError, ConfigError, GmailAuthError
from assistant.gmail_imap import Bookmark, GmailBusy, MailRef, MessageGone, open_mailbox, token_fingerprint
from assistant.vault import save

ROOT = Path(__file__).parent.parent
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


# --- what the code can reach ---------------------------------------------------------------------

def test_nothing_can_send_mail_or_uses_the_old_google_client():
    source = {p: p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py")}
    for name in ("smtplib", "googleapiclient", "google.auth", "google_auth_oauthlib"):
        assert not [p for p, text in source.items() if f"import {name}" in text or f"from {name}" in text], name


def test_only_mailbox_touches_imap():
    users = [p.name for p in (ROOT / "src").rglob("*.py") if "imaplib" in p.read_text(encoding="utf-8")]
    assert sorted(users) == ["gmail_imap.py", "jobs.py"]  # jobs.py only names imaplib.IMAP4.abort


def test_an_old_credentials_file_setting_is_ignored(tmp_path):
    (tmp_path / "config.toml").write_text('[gmail]\ncredentials_file = "credentials.json"\naddress = "me@gmail.com"\n',
                                          encoding="utf-8")
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.gmail_address == "me@gmail.com" and not hasattr(cfg, "credentials_file")


# --- review fixes: the certificate, refusals, the gate, fetch replies, broken mail ----------------

def test_the_connection_checks_gmails_certificate(cfg, fake):
    import ssl

    open_mailbox(cfg)
    assert fake.ssl_context.verify_mode == ssl.CERT_REQUIRED and fake.ssl_context.check_hostname


def test_a_server_with_an_untrusted_certificate_never_receives_the_password(cfg, monkeypatch):
    import socket
    import ssl
    import threading

    data = ROOT / "tests" / "data"
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(data / "self_signed_cert.pem", data / "self_signed_key.pem")
    listener = socket.create_server(("127.0.0.1", 0))
    received = []

    def serve():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                tls.sendall(b"* OK Gimap ready\r\n")
                received.append(tls.recv(4096))
        except (ssl.SSLError, OSError):
            pass  # the client hung up during the handshake: what should happen

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(gmail_imap, "HOST", "localhost")
    monkeypatch.setattr(gmail_imap, "PORT", listener.getsockname()[1])
    save("gmail", PASSWORD)
    with pytest.raises(ssl.SSLCertVerificationError):
        open_mailbox(cfg)
    thread.join(5)
    listener.close()
    assert not any(PASSWORD.encode() in r for r in received)


@pytest.mark.parametrize("greeting, raised", [
    (imaplib.IMAP4.error("b'* BYE [UNAVAILABLE] Temporary System Problem'"), ConnectionError),
    (imaplib.IMAP4.error("b'* NO [THROTTLED] slow down'"), GmailBusy),
    (imaplib.IMAP4.error("b'* BAD what'"), ConnectionError),
])
def test_a_refused_greeting_is_waited_out_like_an_outage(cfg, fake, greeting, raised):
    fake.greeting_error = greeting
    with pytest.raises(raised):
        open_mailbox(cfg)
    assert "LOGIN" not in fake.commands


@pytest.mark.parametrize("answer, raised, says", [
    ("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'", GmailAuthError, "set-key gmail"),
    ("b'[ALERT] Please log in via your web browser (Failure)'", GmailAuthError, "web browser"),
    ("b'[ALERT] Account exceeded command or bandwidth limits. (Failure)'", GmailBusy, ""),
    ("b'Temporary System Error'", ConnectionError, ""),
    ("b'[SERVERBUG] oops'", ConnectionError, ""),
    ("b'Lookup failed'", ConnectionError, ""),
])
def test_only_a_wrong_password_reads_as_a_wrong_password(cfg, fake, answer, raised, says):
    fake.login_error = imaplib.IMAP4.error(answer)
    with pytest.raises(raised) as info:
        open_mailbox(cfg)
    assert says in str(info.value) and PASSWORD not in str(info.value)


@pytest.mark.parametrize("command, args", [
    ("UID SEARCH", ("UID", "1:*\r\nA1 UID STORE 1 +FLAGS (\\Deleted)")),
    ("UID SEARCH", ("X-GM-RAW", '"x"\nA2 DELETE "INBOX"')),
    ("UID FETCH", ("1 2", "(X-GM-MSGID)")),
    ("UID FETCH", ("1", "(X-GM-MSGID)", "BODY[]")),
    ("UID FETCH", ("1\0", "(X-GM-MSGID)")),
])
def test_the_gate_refuses_line_breaks_and_odd_fetch_shapes(cfg, fake, command, args):
    box = open_mailbox(cfg)
    sent = len(fake.commands)
    with pytest.raises(PermissionError):
        box._send(command, *args)
    assert len(fake.commands) == sent


def test_a_label_that_looks_like_a_fetch_field_keeps_its_mail(cfg, fake):
    fake.messages = [FakeMessage(uid=7, msgid=ID_1), FakeMessage(uid=10, msgid=ID_1 + 1, labels='"UID 7" Work')]
    refs = open_mailbox(cfg).list_new(5)
    assert [(r.uid, r.id) for r in refs] == [(7, HEX_1), (10, format(ID_1 + 1, "x"))]
    assert refs[1].labels == ("UID 7", "Work")


def test_a_label_sent_as_a_literal_keeps_its_mail_and_its_body(cfg, fake):
    fake.messages = [FakeMessage(uid=10, msgid=ID_1, labels="Work", labels_literal=b'Odd "label"')]
    box = open_mailbox(cfg)
    refs = box.list_new(5)
    assert [(r.uid, r.labels) for r in refs] == [(10, ('Odd "label"', "Work"))]
    message = box.get_message(HEX_1)
    assert {"name": "Subject", "value": "Hello"} in message["payload"]["headers"]


def test_an_email_the_converter_chokes_on_is_unreadable_not_a_crash(cfg, fake, monkeypatch):
    from assistant.gmail_imap import UnreadableMail

    def choke(*args, **kwargs):
        raise IndexError("string index out of range")

    box = open_mailbox(cfg)
    box.list_new(4)
    monkeypatch.setattr(gmail_imap, "to_api_shape", choke)
    with pytest.raises(UnreadableMail):
        box.get_message(HEX_1)


def test_a_date_before_1970_is_read_without_the_platform_clock(cfg, fake):
    fake.messages = [FakeMessage(uid=5, msgid=ID_1, date="31-Dec-1969 23:59:59 +0000"),
                     FakeMessage(uid=6, msgid=ID_1 + 1, date=" 9-Oct-2025 08:53:20 +0700")]
    box = open_mailbox(cfg)
    box.list_new(4)
    assert box.get_message(HEX_1)["internalDate"] == "0"
    assert box.get_message(format(ID_1 + 1, "x"))["internalDate"] == "1759974800000"


def test_a_refused_body_fetch_is_refused_for_that_message_only(cfg, fake):
    from assistant.gmail_imap import MessageRefused

    fake.refuse_fetch = {5}
    box = open_mailbox(cfg)
    box.list_new(4)
    with pytest.raises(MessageRefused):
        box.get_message(HEX_1)
    assert box.get_message(format(ID_1 + 1, "x"))["id"] == format(ID_1 + 1, "x")

