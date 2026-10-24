"""CLI error-handling tests: expected failures exit cleanly, from one place.

Run: pytest
"""

from pathlib import Path

import pytest
from fake_gmail import FakeGmail

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
    assert sync_mail(cfg, days=3) == (2, 1)  # your own reply is skipped, not stored

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
    from assistant import ingest

    good = make_message("Devpost <hello@devpost.com>", "me@gmail.com", "Hackathon", [], msg_id="good")
    bad = make_message("x@y.com", "me@gmail.com", "Boom", [], msg_id="bad")
    fake = FakeGmail([bad, good], profile="me@gmail.com")
    monkeypatch.setattr(gmail_client, "get_credentials", lambda: object())
    monkeypatch.setattr(gmail_client, "build_service", lambda creds: fake)
    real = ingest.build_mail_item

    def explode_on_bad(raw, *args):
        if raw["id"] == "bad":
            raise ValueError("odd link")
        return real(raw, *args)

    monkeypatch.setattr(ingest, "build_mail_item", explode_on_bad)
    cfg = Config(Path("unused.json"), "newer_than:{days}d", 50, (), tmp_path / "a.db", 4000)
    assert sync_mail(cfg, days=3) == (1, 1)


def _filter_setup(tmp_path):
    from assistant.models import MailItem
    from assistant.store import Store

    (tmp_path / "profile.toml").write_text('[filter]\ndrop_sender_domains = ["discord.com"]\n', encoding="utf-8")
    cfg = Config(Path("unused.json"), "q", 50, (), tmp_path / "a.db", 4000, tmp_path / "profile.toml")
    store = Store(cfg.db_path)
    for mid, subject, domain in (("otp", "Your verification code is 482913", "google.com"),
                                 ("chat", "New message in #general", "discord.com"),
                                 ("jam", "Build Your Game Challenge", "jetbrains.com")):
        store.add(MailItem(mid, "t", "2026-10-14T10:00:00+00:00", f"x@{domain}", domain, True, 1,
                           subject, "s", "body", "other", ["INBOX"]))
    store.commit()
    store.close()
    return cfg


def test_filter_dry_run_changes_nothing(tmp_path, capsys):
    from assistant.cli import run_filter
    from assistant.store import Store

    cfg = _filter_setup(tmp_path)
    assert run_filter(cfg, dry_run=True) == {"one-time-code": 1, "sender:discord.com": 1, "kept": 1}
    assert "would drop [one-time-code]" in capsys.readouterr().out
    assert len(Store(cfg.db_path).queue()) == 3


def test_filter_drops_noise_and_keeps_the_rest(tmp_path):
    from assistant.cli import run_filter
    from assistant.store import Store

    cfg = _filter_setup(tmp_path)
    run_filter(cfg)
    store = Store(cfg.db_path)
    assert [r["message_id"] for r in store.queue()] == ["jam"]
    rows = dict(store.conn.execute("SELECT message_id, status_detail FROM mail_items WHERE status = 'filtered'"))
    assert rows == {"otp": "one-time-code", "chat": "sender:discord.com"}


def _age_all_rows(cfg, days=8):
    from assistant.store import Store
    store = Store(cfg.db_path)
    store.conn.execute(f"UPDATE mail_items SET inserted_at = datetime('now', '-{days} days')")
    store.commit()
    store.close()


def test_expired_mail_is_reported_not_counted_as_kept(tmp_path, capsys):
    from assistant.cli import run_filter
    from assistant.store import Store

    cfg = _filter_setup(tmp_path)
    _age_all_rows(cfg)
    assert run_filter(cfg) == {}  # the sweep ran first: nothing left in the queue
    assert "will not be extracted" in capsys.readouterr().err
    assert {r["status"] for r in Store(cfg.db_path).conn.execute("SELECT status FROM mail_items")} == {"expired"}


def test_a_broken_profile_does_not_stop_the_privacy_sweep(tmp_path):
    from assistant.cli import run_filter
    from assistant.errors import ConfigError
    from assistant.store import Store

    cfg = _filter_setup(tmp_path)
    _age_all_rows(cfg)
    (tmp_path / "profile.toml").write_text("[boosts]\nAI = 20\n", encoding="utf-8")  # typo'd section
    with pytest.raises(ConfigError):
        run_filter(cfg)
    bodies = [r[0] for r in Store(cfg.db_path).conn.execute("SELECT body_text FROM mail_items")]
    assert bodies == ["", "", ""]


def test_sync_also_sweeps_old_bodies(tmp_path, monkeypatch):
    from test_parser import make_message
    from assistant.store import Store

    cfg = _filter_setup(tmp_path)
    _age_all_rows(cfg)
    fake = FakeGmail([make_message("a@devpost.com", "me@gmail.com", "Hackathon", [], msg_id="new1")], "me@gmail.com")
    monkeypatch.setattr(gmail_client, "get_credentials", lambda: object())
    monkeypatch.setattr(gmail_client, "build_service", lambda creds: fake)
    sync_mail(cfg, days=3)
    rows = dict(Store(cfg.db_path).conn.execute("SELECT message_id, status FROM mail_items"))
    assert rows == {"otp": "expired", "chat": "expired", "jam": "expired", "new1": "new"}


def test_a_locked_database_gives_a_clean_message(tmp_path, monkeypatch):
    import sqlite3
    from assistant import cli

    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cli, "Store", locked)
    with pytest.raises(SystemExit) as info:
        main(["--config", str(tmp_path / "config.toml"), "stats"])
    assert "busy" in str(info.value.code)


# --- Step 5: [llm] config and `assistant costs` -------------------------------------

def test_llm_settings_default_to_the_approved_plan(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    llm = load_config(tmp_path / "config.toml").llm
    assert (llm.extract_model, llm.digest_model, llm.digest_effort) == ("claude-haiku-5-5", "claude-opus-5-5", "low")
    assert (llm.caps.monthly_soft_usd, llm.caps.daily_usd) == (5.0, 1.0)


@pytest.mark.parametrize("section, message", [
    ('digest_effort = "lowest"', "digest_effort must be one of"),
    ('daily_cap = 1.0', "unknown key"),
    ('monthly_soft_cap_usd = "5"', "number of dollars"),
    ('monthly_soft_cap_usd = 0', "number of dollars"),
    ('digest_model = "gpt-5"', "Claude model id"),
    ('digest_model = "claude-sonnet-4-6"', "has no price"),         # would be priced at $10/$50
    ('extract_model = "claude-sonnet-5-5"', "extract_model must be one of"),  # 400s with thinking off
    ('extract_model = "claude-haiku-4-5"', "extract_model must be one of"),   # has no effort setting
    ('daily_cap_usd = 0.3', "below one digest's worst case"),        # Opus digest reserves $0.43
    ('monthly_soft_cap_usd = 0.4', "below one digest's worst case"),
])
def test_llm_settings_mistakes_fail_loudly(tmp_path, section, message):
    (tmp_path / "config.toml").write_text(f"[llm]\n{section}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path / "config.toml")


def test_the_cap_error_names_a_figure_that_works(tmp_path):
    # One Opus digest's worst case is $0.672: the message rounds UP, so following it works
    (tmp_path / "config.toml").write_text("[llm]\ndaily_cap_usd = 0.67\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"worst case \(\$0\.68 with claude-opus-5-5\)"):
        load_config(tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("[llm]\ndaily_cap_usd = 0.68\n", encoding="utf-8")
    assert load_config(tmp_path / "config.toml").llm.caps.daily_usd == 0.68


def test_a_dated_model_id_is_accepted(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\ndigest_model = "claude-opus-4-8-20260801"\n', encoding="utf-8")
    assert load_config(tmp_path / "config.toml").llm.digest_model == "claude-opus-4-8-20260801"


def test_costs_command_shows_spend_tokens_and_estimates(tmp_path, capsys):
    import re
    from datetime import datetime, timezone

    from assistant.cli import show_costs
    from assistant.costs import Usage
    from assistant.store import Store

    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    cfg = load_config(tmp_path / "config.toml")
    now = datetime(2026, 10, 15, 3, tzinfo=timezone.utc)
    store = Store(cfg.db_path)

    def call(purpose, model, estimate):
        return store.llm_call(now_utc=now, purpose=purpose, model=model, estimate_usd=estimate, caps=cfg.llm.caps)

    with call("extract", "claude-haiku-5-5", 0.001) as c:     # $0.00034
        c.settle("ok", [("claude-haiku-5-5", Usage(1_900, 300))])
    with call("digest", "claude-opus-5-5", 0.432) as c:       # $0.05
        c.settle("ok", [("claude-opus-5-5", Usage(4_000, 1_700, thinking_tokens=1_000))])
    with pytest.raises(TimeoutError):
        with call("digest", "claude-opus-5-5", 0.432):        # no usage: the $0.432 estimate stays
            raise TimeoutError
    store.close()
    show_costs(cfg, now_utc=now)
    out = capsys.readouterr().out
    assert "this month: $0.4823 of $5.00 soft cap (10%)" in out
    assert re.search(r"digest +claude-opus-5-5 +ok +1 +\$0\.0500 +4,000 +1,700 +1,000 ", out)
    assert re.search(r"extract +claude-haiku-5-5 +ok +1 +\$0\.0003 +1,900 +300 +0 ", out)
    assert re.search(r"digest +claude-opus-5-5 +unknown +1 +\$0\.4320 ", out)
    assert "count at their worst-case estimate" in out
