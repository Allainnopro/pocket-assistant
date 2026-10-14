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


def test_link_audit_reads_raw_links_and_reports_names_only(tmp_path, monkeypatch):
    from test_parser import b64, make_message
    from assistant.cli import link_audit

    html = '<a href="https://www.linkedin.com/comm/jobs/view/42/?otpToken=SECRET&trk=x">AI Intern</a>'
    alert = make_message("j@linkedin.com", "me@gmail.com", "Jobs",
                         [{"mimeType": "text/html", "body": {"data": b64(html)}}], msg_id="a1")
    fake = FakeGmail([alert], profile="me@gmail.com")
    monkeypatch.setattr(gmail_client, "get_credentials", lambda: object())
    monkeypatch.setattr(gmail_client, "build_service", lambda creds: fake)
    cfg = Config(Path("unused.json"), "newer_than:{days}d", 50, (), tmp_path / "a.db", 4000)

    report = link_audit(cfg, days=7)
    assert report[0]["host"] == "www.linkedin.com"
    assert set(report[0]["dropped_params"]) == {"otpToken", "trk"}
    assert "SECRET" not in repr(report)
    assert not (tmp_path / "a.db").exists()  # an audit stores nothing


def test_one_unparseable_email_does_not_stop_the_sync(tmp_path, monkeypatch):
    from test_parser import make_message
    from assistant import cli

    good = make_message("Devpost <hello@devpost.com>", "me@gmail.com", "Hackathon", [], msg_id="good")
    bad = make_message("x@y.com", "me@gmail.com", "Boom", [], msg_id="bad")
    fake = FakeGmail([bad, good], profile="me@gmail.com")
    monkeypatch.setattr(gmail_client, "get_credentials", lambda: object())
    monkeypatch.setattr(gmail_client, "build_service", lambda creds: fake)
    real = cli.build_mail_item

    def explode_on_bad(raw, *args):
        if raw["id"] == "bad":
            raise ValueError("odd link")
        return real(raw, *args)

    monkeypatch.setattr(cli, "build_mail_item", explode_on_bad)
    cfg = Config(Path("unused.json"), "newer_than:{days}d", 50, (), tmp_path / "a.db", 4000)
    assert sync_mail(cfg, days=3) == (1, 0)
