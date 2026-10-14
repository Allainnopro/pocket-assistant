"""CLI error-handling tests: expected failures exit cleanly, from one place.

Run: pytest
"""

from pathlib import Path

import pytest

from assistant import gmail_client
from assistant.cli import build_query, main, sync_mail
from assistant.config import Config
from assistant.config import load_config
from assistant.errors import AssistantError, ConfigError


def test_library_code_raises_a_catchable_error(tmp_path):
    # A plain Exception subclass, so a scheduler's `except Exception` can catch it
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path / "missing.toml")
    assert isinstance(info.value, Exception)
    assert isinstance(info.value, AssistantError)


def test_cli_turns_expected_failures_into_a_clean_exit(tmp_path):
    with pytest.raises(SystemExit) as info:
        main(["--config", str(tmp_path / "missing.toml"), "stats"])
    assert "Config file not found" in str(info.value.code)


def test_query_always_excludes_own_sent_mail_even_if_config_forgets():
    assert build_query("from:devpost.com newer_than:{days}d", 3) == (
        "from:devpost.com newer_than:3d -in:sent -in:drafts"
    )


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeGmail:
    """Just enough of the Gmail API client for sync_mail, recording what it is asked."""

    def __init__(self, messages, profile):
        self.by_id = {m["id"]: m for m in messages}
        self.profile = profile
        self.queries: list[str] = []
        self.profile_calls = 0

    def users(self):
        return self

    def messages(self):
        return self

    def getProfile(self, userId):
        self.profile_calls += 1
        return _Call({"emailAddress": self.profile})

    def list(self, userId, q, pageToken=None, maxResults=None):
        self.queries.append(q)
        return _Call({"messages": [{"id": i} for i in self.by_id]})

    def get(self, userId, id, format):
        return _Call(self.by_id[id])


def test_sync_skips_own_mail_and_marks_mail_to_me(tmp_path, monkeypatch):
    from test_parser import make_message

    incoming = make_message("Devpost <hello@devpost.com>", "Me.Name+devpost@gmail.com", "Hackathon", [], msg_id="in")
    mine = make_message("me.name@gmail.com", "l@uts.edu.au", "My reply", [], msg_id="mine")
    mine["labelIds"] = ["SENT"]
    no_labels = make_message("a@b.com", "list@b.com", "Newsletter", [], msg_id="nolabels")
    del no_labels["labelIds"]
    fake = FakeGmail([incoming, mine, no_labels], profile="Me.Name@Gmail.com")
    monkeypatch.setattr(gmail_client, "get_credentials", lambda: object())
    monkeypatch.setattr(gmail_client, "build_service", lambda creds: fake)

    cfg = Config(Path("unused.json"), "newer_than:{days}d", 50, ("me@uni.edu.vn",), tmp_path / "a.db", 4000)
    assert sync_mail(cfg, days=3) == (2, 0)

    assert fake.queries == ["newer_than:3d -in:sent -in:drafts"]
    assert fake.profile_calls == 1
    from assistant.store import Store
    store = Store(cfg.db_path)
    assert store.has("in") and store.has("nolabels") and not store.has("mine")
    rows = dict(store.conn.execute("SELECT message_id, to_me FROM mail_items").fetchall())
    assert rows == {"in": 1, "nolabels": 0}
