"""Gmail over IMAP, read-only. The only code that touches the mailbox.

The owner chose an App Password over OAuth on 2026-11-03 to end the weekly sign-in.
The cost: Google no longer enforces read-only, because the password has full mailbox
access. So this module keeps that promise itself:

- Every command goes through Mailbox._send, whose allow-list holds read commands only.
- A UID FETCH may only ask for items that change nothing: BODY.PEEK, never BODY[] or
  RFC822, which mark an email as read.
- All Mail is opened with EXAMINE (select(readonly=True)), so the server refuses
  changes on this connection too.
- The connection checks Gmail's certificate against Windows' own store, as llm.py's
  does. Python's imaplib checks nothing by default, which would hand the password,
  and with it the whole mailbox, to any machine that answered in Gmail's place.

Spam and the bin aren't in All Mail. A message moved out of them joins All Mail with
a new, higher UID, so the normal "UIDs above the bookmark" check finds rescued mail.
X-GM-MSGID and X-GM-THRID are the decimal form of the Gmail API's hex ids, so ids
stored before the switch still match, and Gmail links keep working.

Nothing here logs. No error message holds the password.
"""

from __future__ import annotations

import calendar
import hashlib
import imaplib
import re
import ssl
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import keyring
import truststore

from .config import Config
from .errors import AssistantError, ConfigError, GmailAuthError, SecretError
from .gmail_message import to_api_shape
from .vault import KEYRING_SERVICE, SECRETS, load

HOST, PORT, TIMEOUT_SECONDS = "imap.gmail.com", 993, 30
ALLOWED_COMMANDS = frozenset({"LOGIN", "LIST", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"})
BODY_ITEM = "BODY.PEEK[]<0.2000000>"  # the first 2 MB: text comes before attachments
ALLOWED_FETCH_ITEMS = frozenset({"X-GM-MSGID", "X-GM-THRID", "X-GM-LABELS", "INTERNALDATE", BODY_ITEM})
REFS_ITEMS = "(X-GM-MSGID X-GM-LABELS)"
MESSAGE_ITEMS = f"(X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE {BODY_ITEM})"
BATCH = 500
LABELS = {"\\Inbox": "INBOX", "\\Sent": "SENT", "\\Draft": "DRAFT", "\\Drafts": "DRAFT", "\\Important": "IMPORTANT",
          "Important": "IMPORTANT", "\\Starred": "STARRED"}
CATEGORIES = {"primary": "CATEGORY_PERSONAL", "social": "CATEGORY_SOCIAL", "promotions": "CATEGORY_PROMOTIONS",
              "updates": "CATEGORY_UPDATES", "forums": "CATEGORY_FORUMS"}
BUSY_MARKS = ("OVERQUOTA", "THROTTLED", "Too many simultaneous connections", "bandwidth limits")
PASSWORD_REFUSED = "Gmail refused the App Password. Run: assistant set-key gmail"
WEB_SIGN_IN = ("Gmail wants you to sign in once in a web browser: open https://mail.google.com "
               "and sign in. Mail checks try again within the hour.")
MONTHS = {name: n for n, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                            "Nov", "Dec"), start=1)}
INTERNALDATE = re.compile(r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)")
ALL_MAIL_HIDDEN = ("Gmail isn't showing All Mail over IMAP. In Gmail: Settings -> Labels -> "
                   'tick "Show in IMAP" next to All Mail.')


@dataclass(frozen=True)
class Bookmark:
    uidvalidity: str
    last_uid: int


@dataclass(frozen=True)
class MailRef:
    id: str  # hex, as stored
    uid: int
    labels: tuple[str, ...]


class MessageGone(Exception):
    """Deleted or moved after it was listed, or never listed on this connection."""


class GmailBusy(Exception):
    """Gmail is limiting this account for now."""


class MessageRefused(Exception):
    """Gmail answered NO for this one message. Often temporary: try it again later."""


class UnreadableMail(Exception):
    """The email itself can't be read (broken MIME, a parser limit): skip it, never stall on it.
    args: (message id, the error's type name)."""


def token_fingerprint() -> bytes | None:
    """A hash of the saved App Password (never the password), so a running engine
    notices a new one. None when there is none."""
    saved = keyring.get_password(KEYRING_SERVICE, SECRETS["gmail"].entry)
    return hashlib.sha256(saved.encode()).digest() if saved else None


def open_mailbox(cfg: Config) -> Mailbox:
    """Signs in and opens All Mail, read-only. Raises GmailAuthError when the address or
    password is missing or refused, OSError when Gmail can't be reached."""
    if not cfg.gmail_address:
        raise GmailAuthError('Set [gmail] address in config.toml, e.g. address = "you@gmail.com"')
    try:
        password = load("gmail")
    except SecretError:
        raise GmailAuthError("No Gmail App Password saved yet. Run: assistant set-key gmail") from None
    try:
        conn = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=_tls(), timeout=TIMEOUT_SECONDS)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error as exc:  # refused at the greeting, before any password was sent
        _raise_if_known(str(exc))
        raise ConnectionError("Gmail refused the connection for now") from None
    box = Mailbox(conn, cfg.gmail_address)
    try:
        box._sign_in(password)
        box._open_all_mail()
    except BaseException:
        box.close()
        raise
    return box


def _tls() -> ssl.SSLContext:
    """Certificate and host name both checked, against Windows' store (which fetches a
    missing root itself) rather than a bundle shipped with Python."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _raise_if_known(text: str) -> None:
    if any(mark in text for mark in BUSY_MARKS):
        raise GmailBusy(text[:200])
    if "UNAVAILABLE" in text:
        raise ConnectionError(text[:200])


def _meta_of(item) -> bytes:
    return item[0] if isinstance(item, tuple) else item


_OPEN, _CLOSE = object(), object()
_TOKEN = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _tokens(data: list) -> list:
    """imaplib's FETCH answer -> tokens. imaplib splits an answer at each literal ({n}
    bytes that follow), so the pieces are read as one stream; a literal stays bytes."""
    tokens: list = []
    for item in data:
        if isinstance(item, tuple):
            meta, literal = item
            pieces = [re.sub(rb"\{\d+\}$", b"", meta), literal]
        elif isinstance(item, bytes):
            pieces = [item]
        else:
            continue
        for n, piece in enumerate(pieces):
            if n == 1:
                tokens.append(piece)
                continue
            for found in _TOKEN.finditer(piece):
                opened, closed, quoted, atom = found.groups()
                if opened:
                    tokens.append(_OPEN)
                elif closed:
                    tokens.append(_CLOSE)
                elif quoted is not None:
                    tokens.append(re.sub(rb"\\(.)", rb"\1", quoted).decode("utf-8", "replace"))
                elif atom:
                    tokens.append(atom.decode("utf-8", "replace"))
    return tokens


def _fetch_replies(data: list) -> list[dict]:
    """One dict per message: item name -> value, read at the top level only, so a label
    named "UID 7" stays a label. A list item (X-GM-LABELS) becomes a list."""
    tokens, replies, i = _tokens(data), [], 0

    def value(i: int):
        if tokens[i] is not _OPEN:
            return tokens[i], i + 1
        items, i = [], i + 1
        while i < len(tokens) and tokens[i] is not _CLOSE:
            item, i = value(i)
            items.append(item)
        return items, i + 1

    while i < len(tokens):
        if tokens[i] is not _OPEN:  # the message's sequence number
            i += 1
            continue
        reply, i = {}, i + 1
        while i + 1 < len(tokens) and tokens[i] is not _CLOSE:
            name = tokens[i]
            item, i = value(i + 1)
            if isinstance(name, str):
                reply[name.upper()] = item
        replies.append(reply)
        i += 1
    return replies


def _int(value) -> int | None:
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _labels(value) -> tuple[str, ...]:
    """X-GM-LABELS, mapped to the Gmail API's names."""
    if not isinstance(value, list):
        return ()
    names = [v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in value]
    return tuple(LABELS.get(name, name) for name in names if isinstance(name, str))


def _epoch_ms(value) -> int:
    """INTERNALDATE ("09-Oct-2025 08:53:20 +0000") -> milliseconds, by arithmetic: Windows'
    clock functions refuse dates before 1970. Unreadable or before 1970 -> 0."""
    found = INTERNALDATE.fullmatch(value) if isinstance(value, str) else None
    if not found or found.group(2).title() not in MONTHS:
        return 0
    day, month, year, hh, mm, ss, sign, oh, om = found.groups()
    seconds = calendar.timegm((int(year), MONTHS[month.title()], int(day), int(hh), int(mm), int(ss)))
    seconds -= (1 if sign == "+" else -1) * (int(oh) * 3600 + int(om) * 60)
    return max(0, seconds * 1000)


def _batches(items: list[int]) -> Iterator[list[int]]:
    for start in range(0, len(items), BATCH):
        yield items[start:start + BATCH]


def _uid_set(uids: Iterable[int]) -> str:
    return ",".join(str(u) for u in uids)


class Mailbox:
    """One signed-in connection, for one check. Use it from one thread."""

    def __init__(self, conn, address: str) -> None:
        self._conn, self.address = conn, address
        self._uidvalidity, self._uidnext = "", 1
        self._listed: dict[str, MailRef] = {}

    def __enter__(self) -> Mailbox:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _send(self, command: str, *args):
        """The one gate to the server: anything but a read stops here."""
        if command not in ALLOWED_COMMANDS:
            raise PermissionError(f"{command} isn't allowed: the mailbox is read-only")
        if any(ch in str(arg) for arg in args for ch in "\r\n\0"):  # a line break would start a second command
            raise PermissionError(f"{command} can't carry a line break")
        if command == "UID FETCH" and (len(args) != 2 or not re.fullmatch(r"[0-9,:*]+", args[0])
                                       or not ALLOWED_FETCH_ITEMS.issuperset(args[1].strip("()").split())):
            raise PermissionError("That fetch isn't allowed: it could change the mailbox")
        call = {"LOGIN": self._conn.login, "LIST": self._conn.list, "LOGOUT": self._conn.logout,
                "EXAMINE": lambda folder: self._conn.select(folder, readonly=True),
                "UID SEARCH": lambda *a: self._conn.uid("SEARCH", *a),
                "UID FETCH": lambda *a: self._conn.uid("FETCH", *a)}[command]
        try:
            kind, data = call(*args)
        except imaplib.IMAP4.error as exc:  # abort is a kind of error
            _raise_if_known(str(exc))
            raise
        if kind not in ("OK", "BYE"):
            text = repr(data)
            _raise_if_known(text)
            raise imaplib.IMAP4.error(f"{command} answered {kind}: {text[:200]}")
        return data

    def _sign_in(self, password: str) -> None:
        try:
            self._send("LOGIN", self.address, password)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as exc:  # its text comes from the server; ours says what to do
            text = str(exc)
            if "AUTHENTICATIONFAILED" in text or "Invalid credentials" in text:
                raise GmailAuthError(PASSWORD_REFUSED) from None
            if "WEBALERT" in text or "web browser" in text:
                raise GmailAuthError(WEB_SIGN_IN) from None
            # Anything else ("Temporary System Error", [SERVERBUG]...) is Gmail's own trouble:
            # waited out like an outage, never blamed on a password that works
            raise ConnectionError("Gmail refused the sign-in for now") from None

    def _open_all_mail(self) -> None:
        for line in self._send("LIST"):
            found = re.match(rb"\(([^)]*)\)\s+\S+\s+(.+)$", _meta_of(line) or b"")
            if found and rb"\All" in found.group(1).split():
                self._send("EXAMINE", found.group(2).decode("ascii", "replace"))
                self._uidvalidity = self._conn.response("UIDVALIDITY")[1][0].decode()
                nxt = self._conn.response("UIDNEXT")[1][0]
                self._uidnext = int(nxt) if nxt else 1
                return
        raise AssistantError(ALL_MAIL_HIDDEN)

    def bookmark(self) -> Bookmark:
        return Bookmark(self._uidvalidity, self._uidnext - 1)

    def list_new(self, after_uid: int) -> list[MailRef]:
        """UID n:* always answers with the newest message, even when n is past it."""
        found = self._send("UID SEARCH", "UID", f"{after_uid + 1}:*")
        return self._refs(sorted(u for u in self._uids_in(found) if u > after_uid))

    def search(self, query: str, limit: int) -> list[MailRef]:
        """The newest `limit` messages that a Gmail search finds, oldest first."""
        if not query.isascii() or not query.isprintable():
            raise ConfigError("[gmail] query must be plain English letters and symbols: IMAP can't send anything else")
        quoted = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return self._refs(sorted(self._uids_in(self._send("UID SEARCH", "X-GM-RAW", quoted)))[-limit:])

    @staticmethod
    def _uids_in(found: list) -> list[int]:
        return [int(u) for u in (found[0] or b"").split()]

    def _refs(self, uids: list[int]) -> list[MailRef]:
        if not uids:
            return []
        ids: dict[int, str] = {}
        labels: dict[int, list[str]] = {}
        for batch in _batches(uids):
            for reply in _fetch_replies(self._send("UID FETCH", _uid_set(batch), REFS_ITEMS)):
                uid, msgid = _int(reply.get("UID")), _int(reply.get("X-GM-MSGID"))
                if uid is not None and msgid is not None:
                    ids[uid], labels[uid] = format(msgid, "x"), list(_labels(reply.get("X-GM-LABELS")))
            for name, label in CATEGORIES.items():
                hits = self._send("UID SEARCH", "UID", _uid_set(batch), "X-GM-RAW", f'"category:{name}"')
                for uid in self._uids_in(hits):
                    if uid in labels:
                        labels[uid].append(label)
        refs = [MailRef(ids[u], u, tuple(labels[u])) for u in uids if u in ids]
        self._listed.update((r.id, r) for r in refs)
        return refs

    def get_message(self, message_id: str) -> dict:
        ref = self._listed.get(message_id)
        if ref is None:
            raise MessageGone(message_id)
        try:
            data = self._send("UID FETCH", str(ref.uid), MESSAGE_ITEMS)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:  # a NO for this message alone; busy and down were raised already
            raise MessageRefused(message_id) from None
        reply = next((r for r in _fetch_replies(data) if isinstance(r.get("BODY[]<0>"), bytes)), None)
        if reply is None:
            raise MessageGone(message_id)
        try:
            return to_api_shape(reply["BODY[]<0>"], message_id=message_id,
                                thread_id=format(_int(reply.get("X-GM-THRID")) or 0, "x"),
                                labels=list(ref.labels), internal_date_ms=_epoch_ms(reply.get("INTERNALDATE")))
        except Exception as exc:  # the email itself is broken: skip it rather than stall on it
            raise UnreadableMail(message_id, type(exc).__name__) from None

    def close(self) -> None:
        try:
            self._send("LOGOUT")
        except (OSError, imaplib.IMAP4.error, GmailBusy):
            pass  # the connection is already gone
