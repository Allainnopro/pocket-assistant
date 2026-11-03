"""Turning Gmail message ids into stored mail: what is kept, skipped and logged.

Run: pytest
"""

from pathlib import Path

from fake_mailbox import FakeMailbox
from test_parser import make_message

from assistant.config import Config
from assistant.ingest import ingest_ids
from assistant.store import Store


def labelled(msg_id, label):
    message = make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id=msg_id)
    message["labelIds"] = [label]
    return message


def test_ingest_skips_stored_spam_trash_chat_and_deleted_messages(tmp_path):
    cfg = Config(Path("unused.json"), "newer_than:{days}d", 50, (), tmp_path / "a.db", 4000)
    store = Store(cfg.db_path)
    old = make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id="old")
    fake = FakeMailbox([old, make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id="new"),
                      labelled("spam1", "SPAM"), labelled("chat1", "CHAT"),
                      make_message("Devpost <hello@devpost.com>", "me@x.com", "Hackathon", [], msg_id="gone")],)
    assert ingest_ids(store, fake, ["old"], cfg, ("me@x.com",), log=lambda line: None) == (1, 0)
    fake.delete("gone")
    lines = []
    ids = ["new", "old", "spam1", "chat1", "gone"]
    assert ingest_ids(store, fake, ids, cfg, ("me@x.com",), log=lines.append) == (1, 4)
    assert store.has("new") and not store.has("spam1") and not store.has("chat1") and not store.has("gone")
    assert all("Hackathon" not in line for line in lines)  # logs carry ids, never subjects or bodies
