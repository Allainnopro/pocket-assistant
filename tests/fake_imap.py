"""A stand-in for imaplib.IMAP4_SSL: Gmail's All Mail, answering in imaplib's real formats.

It has only the methods Mailbox may use: any other attribute fails the test, and so
does opening a folder for writing. Task 4 of the plan checked these formats against
the real server. Replies are built as imaplib returns them, including a fetch's
(meta, literal) tuples followed by a closing b")".
"""

from __future__ import annotations

import imaplib
from dataclasses import dataclass, field

ALL_MAIL = '"[Gmail]/All Mail"'
SIMPLE = (b"From: Devpost <hello@devpost.com>\r\nTo: me@gmail.com\r\nSubject: Hello\r\n"
          b"Content-Type: text/plain; charset=utf-8\r\n\r\nThe body text.\r\n")


@dataclass
class FakeMessage:
    uid: int
    msgid: int
    thrid: int = 99
    labels: str = '"\\\\Inbox"'  # as Gmail writes them: ("\\Inbox" Work "My label")
    raw: bytes = SIMPLE
    date: str = "09-Oct-2025 08:53:20 +0000"
    labels_literal: bytes | None = None  # a label Gmail sends as a literal: ({n}<bytes> ...)


@dataclass
class FakeImap:
    messages: list[FakeMessage] = field(default_factory=list)
    uidvalidity: bytes = b"7"
    uidnext: bytes | None = None
    folders: list[bytes] = field(default_factory=lambda: [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\All) "/" ' + ALL_MAIL.encode(),
        b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
    ])
    categories: dict[str, list[int]] = field(default_factory=dict)
    search_hits: list[int] | None = None  # what X-GM-RAW finds; None: everything
    refuse_login: bool = False
    login_error: Exception | None = None   # raised by login() instead of succeeding
    greeting_error: Exception | None = None  # raised while connecting, as imaplib does for a bad greeting
    refuse_fetch: set[int] = field(default_factory=set)  # body fetches of these UIDs answer NO
    fail_with: Exception | None = None     # raised by the next uid()
    commands: list[str] = field(default_factory=list)
    credentials: tuple[str, str] | None = None
    opened: str | None = None
    connected_with: tuple | None = None
    ssl_context: object = None
    logged_out: bool = False

    def __call__(self, host, port, *, ssl_context=None, timeout=None):  # the "constructor", for IMAP4_SSL
        self.connected_with, self.ssl_context = (host, port, timeout), ssl_context
        if self.greeting_error is not None:
            raise self.greeting_error
        return self

    def login(self, user, password):
        self.commands.append("LOGIN")
        if self.login_error is not None:
            raise self.login_error
        if self.refuse_login:
            raise imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'")
        self.credentials = (user, password)
        return "OK", [b"logged in"]

    def list(self):
        self.commands.append("LIST")
        return "OK", list(self.folders)

    def select(self, mailbox, readonly=False):
        assert readonly is True, "the mailbox must be opened read-only"
        self.commands.append(f"EXAMINE {mailbox}")
        self.opened = mailbox
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        if code == "UIDVALIDITY":
            return code, [self.uidvalidity]
        if code == "UIDNEXT":
            nxt = self.uidnext or str(max((m.uid for m in self.messages), default=0) + 1).encode()
            return code, [nxt]
        raise AssertionError(f"unexpected response code {code}")

    def uid(self, command, *args):
        self.commands.append(f"UID {command} " + " ".join(str(a) for a in args))
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        if command == "SEARCH":
            return self._search(args)
        if command == "FETCH":
            return self._fetch(*args)
        raise AssertionError(f"Mailbox sent UID {command}")

    def _search(self, args):
        if args[0] == "UID" and len(args) == 2:  # UID n:*: always holds the newest, even past n
            first = int(args[1].split(":")[0])
            uids = sorted(m.uid for m in self.messages)
            hits = [u for u in uids if u >= first] or uids[-1:]
        elif args[0] == "UID":  # UID <set> X-GM-RAW "category:x"
            wanted = {int(u) for u in args[1].split(",")}
            name = args[3].strip('"').removeprefix("category:")
            hits = [u for u in self.categories.get(name, []) if u in wanted]
        elif args[0] == "X-GM-RAW":
            hits = self.search_hits if self.search_hits is not None else sorted(m.uid for m in self.messages)
        else:
            raise AssertionError(f"unexpected search {args}")
        return "OK", [" ".join(str(u) for u in hits).encode()]

    def _fetch(self, uids, items):
        wanted = {int(u) for u in uids.split(",")}
        if "BODY.PEEK" in items and wanted & self.refuse_fetch:
            return "NO", [b"System Error (Failure)"]
        replies = []
        for n, m in enumerate(self.messages, start=1):
            if m.uid not in wanted:
                continue
            if m.labels_literal is not None:  # imaplib splits the reply at the literal
                start = f"{n} (X-GM-THRID {m.thrid} X-GM-MSGID {m.msgid} X-GM-LABELS ({{{len(m.labels_literal)}}}"
                replies.append((start.encode(), m.labels_literal))
                meta = f" {m.labels}) UID {m.uid}"
            else:
                meta = f"{n} (X-GM-THRID {m.thrid} X-GM-MSGID {m.msgid} X-GM-LABELS ({m.labels}) UID {m.uid}"
            if "BODY.PEEK" in items:
                head = f'{meta} INTERNALDATE "{m.date}" BODY[]<0> {{{len(m.raw)}}}'
                replies += [(head.encode(), m.raw), b")"]
            else:
                replies.append(f"{meta})".encode())
        return "OK", replies or [None]

    def logout(self):
        self.commands.append("LOGOUT")
        self.logged_out = True
        return "BYE", [b"bye"]
