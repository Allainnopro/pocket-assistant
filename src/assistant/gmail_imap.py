"""Gmail over IMAP, read-only. The only code that touches the mailbox.

The owner chose an App Password over OAuth on 2026-11-03 to end the weekly sign-in.
The cost: Google no longer enforces read-only, because the password has full mailbox
access. So this module keeps that promise itself:

- Every command goes through Mailbox._send, whose allow-list holds read commands only.
- A UID FETCH may only ask for items that change nothing: BODY.PEEK, never BODY[] or
  RFC822, which mark an email as read.
- All Mail is opened with EXAMINE (select(readonly=True)), so the server refuses
  changes on this connection too.

Spam and the bin aren't in All Mail. A message moved out of them joins All Mail with
a new, higher UID, so the normal "UIDs above the bookmark" check finds rescued mail.
X-GM-MSGID and X-GM-THRID are the decimal form of the Gmail API's hex ids, so ids
stored before the switch still match, and Gmail links keep working.

Nothing here logs. No error message holds the password.
"""

from __future__ import annotations

import hashlib
import imaplib
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import keyring

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
BUSY_MARKS = ("OVERQUOTA", "THROTTLED", "Too many simultaneous connections")
PASSWORD_REFUSED = "Gmail refused the App Password. Run: assistant set-key gmail"
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
    box = Mailbox(imaplib.IMAP4_SSL(HOST, PORT, timeout=TIMEOUT_SECONDS), cfg.gmail_address)
    try:
        box._sign_in(password)
        box._open_all_mail()
    except BaseException:
        box.close()
        raise
    return box


def _raise_if_known(text: str) -> None:
    if any(mark in text for mark in BUSY_MARKS):
        raise GmailBusy(text[:200])
    if "UNAVAILABLE" in text:
        raise ConnectionError(text[:200])


def _meta_of(item) -> bytes:
    return item[0] if isinstance(item, tuple) else item


def _number(name: str, meta: bytes) -> int | None:
    found = re.search(rb"\b" + name.encode() + rb" (\d+)", meta)
    return int(found.group(1)) if found else None


def _labels_in(meta: bytes) -> tuple[str, ...]:
    """X-GM-LABELS ("\\\\Inbox" Work "My (label)") -> the labels, mapped to the Gmail API's names."""
    start = meta.find(b"X-GM-LABELS (")
    if start < 0:
        return ()
    text = meta[start + len(b"X-GM-LABELS ("):].decode("utf-8", "replace")
    labels, token, quoted, escaped, in_token = [], [], False, False, False
    for ch in text:
        if escaped:
            token.append(ch)
            escaped = False
        elif quoted and ch == "\\":
            escaped = True
        elif ch == '"':
            quoted, in_token = not quoted, True
        elif not quoted and ch in " )":
            if in_token:
                labels.append("".join(token))
            token, in_token = [], False
            if ch == ")":
                break
        else:
            token.append(ch)
            in_token = True
    return tuple(LABELS.get(label, label) for label in labels)


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
        if command == "UID FETCH" and not ALLOWED_FETCH_ITEMS.issuperset(args[1].strip("()").split()):
            raise PermissionError(f"Fetching {args[1]} isn't allowed: it could change the mailbox")
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
        except imaplib.IMAP4.error:  # its text comes from the server; ours says what to do
            raise GmailAuthError(PASSWORD_REFUSED) from None

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
            for item in self._send("UID FETCH", _uid_set(batch), REFS_ITEMS):
                meta = _meta_of(item)
                if not meta or meta == b")":
                    continue
                uid, msgid = _number("UID", meta), _number("X-GM-MSGID", meta)
                if uid is not None and msgid is not None:
                    ids[uid], labels[uid] = format(msgid, "x"), list(_labels_in(meta))
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
        data = self._send("UID FETCH", str(ref.uid), MESSAGE_ITEMS)
        body = next((item for item in data if isinstance(item, tuple)), None)
        if body is None:
            raise MessageGone(message_id)
        meta, raw = body
        when = imaplib.Internaldate2tuple(meta)
        return to_api_shape(raw, message_id=message_id, thread_id=format(_number("X-GM-THRID", meta) or 0, "x"),
                            labels=list(ref.labels), internal_date_ms=int(time.mktime(when)) * 1000 if when else 0)

    def close(self) -> None:
        try:
            self._send("LOGOUT")
        except (OSError, imaplib.IMAP4.error, GmailBusy):
            pass  # the connection is already gone
