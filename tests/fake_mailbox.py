"""A stand-in for gmail_imap.Mailbox, for the code above it: All Mail as a list of
messages in the shape parser.py reads (test_parser.make_message), with UIDs 1, 2, 3...
It records what it is asked. Mailbox itself is tested against fake_imap.FakeImap."""

from __future__ import annotations

from assistant.gmail_imap import Bookmark, MailRef, MessageGone


class FakeMailbox:
    def __init__(self, messages=(), address: str = "me@x.com", uidvalidity: str = "1") -> None:
        self.address, self.uidvalidity = address, uidvalidity
        self.by_uid: dict[int, dict] = {}
        self.next_uid = 1
        self.deleted: set[str] = set()
        self.get_failures: dict[str, Exception] = {}  # raised by get_message for that id
        self.calls: list[str] = []
        self.queries: list[str] = []
        self.opens = self.closes = 0
        for message in messages:
            self.add(message)

    def __enter__(self):
        self.opens += 1
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.closes += 1

    def add(self, message: dict) -> int:
        uid, self.next_uid = self.next_uid, self.next_uid + 1
        self.by_uid[uid] = message
        return uid

    def delete(self, message_id: str) -> None:
        """Gone from the server after it was listed."""
        self.deleted.add(message_id)

    def _ref(self, uid: int) -> MailRef:
        message = self.by_uid[uid]
        return MailRef(message["id"], uid, tuple(message.get("labelIds", ())))

    def bookmark(self) -> Bookmark:
        self.calls.append("bookmark")
        return Bookmark(self.uidvalidity, self.next_uid - 1)

    def list_new(self, after_uid: int) -> list[MailRef]:
        self.calls.append("list_new")
        return [self._ref(uid) for uid in sorted(self.by_uid) if uid > after_uid]

    def search(self, query: str, limit: int) -> list[MailRef]:
        self.calls.append("search")
        self.queries.append(query)
        return [self._ref(uid) for uid in sorted(self.by_uid)[-limit:]]

    def get_message(self, message_id: str) -> dict:
        self.calls.append("get_message")
        if message_id in self.get_failures:
            raise self.get_failures[message_id]
        if message_id in self.deleted:
            raise MessageGone(message_id)
        return next(m for m in self.by_uid.values() if m["id"] == message_id)
