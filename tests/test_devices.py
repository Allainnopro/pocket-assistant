"""Pairing a device with the local API, and its key.

Run: pytest
"""

import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assistant.cli import main
from assistant.config import load_config
from assistant.engine.devices import device_for_key, list_devices, new_pairing_code, redeem_code, revoke
from assistant.engine.lock import EngineLock, lock_path
from assistant.errors import PairingError
from assistant.store import Store

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "a.db")
    yield store
    store.close()


def test_a_code_pairs_one_device_once(store):
    code = new_pairing_code(store, NOW)
    assert len(code) == 6 and code.isdigit()
    device_id, key = redeem_code(store, code, "laptop", NOW + MIN)
    assert device_for_key(store, key, NOW + MIN) == device_id
    with pytest.raises(PairingError):
        redeem_code(store, code, "phone", NOW + 2 * MIN)  # single use
    assert [r["name"] for r in list_devices(store)] == ["laptop"]


def test_a_code_expires_after_5_minutes(store):
    code = new_pairing_code(store, NOW)
    with pytest.raises(PairingError, match="expired"):
        redeem_code(store, code, "laptop", NOW + timedelta(minutes=5, seconds=1))


def test_a_code_still_works_at_the_end_of_its_5_minutes(store):
    code = new_pairing_code(store, NOW)
    assert redeem_code(store, code, "laptop", NOW + timedelta(minutes=5))


def wrong_for(code):
    return "000000" if code != "000000" else "111111"


def test_five_wrong_tries_kill_the_code(store):
    code = new_pairing_code(store, NOW)
    for _ in range(5):
        with pytest.raises(PairingError):
            redeem_code(store, wrong_for(code), "x", NOW)
    with pytest.raises(PairingError):
        redeem_code(store, code, "x", NOW)  # even the right code is dead now


def test_four_wrong_tries_leave_the_code_alive(store):
    code = new_pairing_code(store, NOW)
    for _ in range(4):
        with pytest.raises(PairingError):
            redeem_code(store, wrong_for(code), "x", NOW)
    assert redeem_code(store, code, "x", NOW)


def test_a_new_code_replaces_the_old_one_and_resets_the_tries(store):
    old = new_pairing_code(store, NOW)
    for _ in range(4):
        with pytest.raises(PairingError):
            redeem_code(store, wrong_for(old), "x", NOW)
    new = new_pairing_code(store, NOW)
    if old != new:
        with pytest.raises(PairingError):
            redeem_code(store, old, "x", NOW)
    assert redeem_code(store, new, "x", NOW)


def test_only_a_hash_of_the_key_and_the_code_is_stored(store):
    code = new_pairing_code(store, NOW)
    assert code not in str([tuple(r) for r in store.conn.execute("SELECT key, value FROM state")])
    _, key = redeem_code(store, code, "laptop", NOW)
    assert key not in str([tuple(r) for r in store.conn.execute("SELECT * FROM devices")])


def test_a_refusal_never_repeats_the_code(store):
    new_pairing_code(store, NOW)
    with pytest.raises(PairingError) as info:
        redeem_code(store, "98x76y", "x", NOW)
    assert "98x76y" not in str(info.value)


def test_an_unknown_key_is_nobody(store):
    assert device_for_key(store, "nope", NOW) is None


def test_a_revoked_key_stops_working_at_once(store):
    device_id, key = redeem_code(store, new_pairing_code(store, NOW), "laptop", NOW)
    assert revoke(store, device_id, NOW) and device_for_key(store, key, NOW) is None
    assert revoke(store, device_id, NOW) is False  # already revoked
    assert revoke(store, 99, NOW) is False


def test_last_seen_is_written_at_most_once_a_minute(store):
    device_id, key = redeem_code(store, new_pairing_code(store, NOW), "laptop", NOW)
    seen = lambda: store.conn.execute("SELECT last_seen_at FROM devices").fetchone()[0]
    device_for_key(store, key, NOW)
    first = seen()
    assert first == NOW.isoformat()
    device_for_key(store, key, NOW + timedelta(seconds=30))
    assert seen() == first
    device_for_key(store, key, NOW + timedelta(seconds=61))
    assert seen() == (NOW + timedelta(seconds=61)).isoformat()


@pytest.mark.parametrize("name, kept", [("  laptop  ", "laptop"), ("", "device"), ("a\nb\x07c", "abc"), ("x" * 99, "x" * 40)])
def test_a_device_name_is_tidied(store, name, kept):
    redeem_code(store, new_pairing_code(store, NOW), name, NOW)
    assert list_devices(store)[0]["name"] == kept


@pytest.fixture
def config_file(tmp_path):
    (tmp_path / "config.toml").write_text('[api]\ntailnet_host = "pc.tail1234.ts.net"\n', encoding="utf-8")
    shutil.copy(ROOT / "profile.example.toml", tmp_path / "profile.toml")
    return tmp_path / "config.toml"


def test_pair_and_devices_work_while_the_engine_runs(config_file, capsys):
    cfg = load_config(config_file)
    with EngineLock(lock_path(cfg)):
        main(["--config", str(config_file), "pair"])
        out = capsys.readouterr().out
        assert re.search(r"\b\d{6}\b", out)
        assert "Invoke-RestMethod" in out and "https://pc.tail1234.ts.net/api/pair" in out
        main(["--config", str(config_file), "devices"])
        assert "No devices" in capsys.readouterr().out
        with pytest.raises(SystemExit) as info:
            main(["--config", str(config_file), "devices", "revoke", "99"])
        assert "No device 99" in str(info.value.code)


def test_pair_says_what_is_missing_before_tailscale_is_set_up(tmp_path, capsys):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    main(["--config", str(tmp_path / "config.toml"), "pair"])
    out = capsys.readouterr().out
    assert "[api] tailnet_host" in out and "reach the engine" in out and "this PC" not in out


def test_devices_lists_and_revokes(config_file, capsys):
    cfg = load_config(config_file)
    store = Store(cfg.db_path)
    device_id, key = redeem_code(store, new_pairing_code(store, NOW), "laptop", NOW)
    store.close()
    main(["--config", str(config_file), "devices"])
    assert "laptop" in capsys.readouterr().out
    main(["--config", str(config_file), "devices", "revoke", str(device_id)])
    assert "Revoked" in capsys.readouterr().out
    store = Store(cfg.db_path)
    assert device_for_key(store, key, NOW) is None
    store.close()


def test_a_busy_database_never_holds_up_a_good_key(store):  # M2 final review: mail import, extraction
    import sqlite3
    import time

    device_id, key = redeem_code(store, new_pairing_code(store, NOW), "laptop", NOW)
    writer = sqlite3.connect(store.db_path, timeout=0)
    writer.execute("BEGIN IMMEDIATE")  # another connection is writing
    try:
        started = time.monotonic()
        assert device_for_key(store, key, NOW) == device_id
        assert time.monotonic() - started < 1  # the "last seen" note is skipped, not waited for
    finally:
        writer.rollback()
        writer.close()
    assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000  # back for everything else
    device_for_key(store, key, NOW + timedelta(seconds=1))  # not due yet by time, but none was written
    assert store.conn.execute("SELECT last_seen_at FROM devices").fetchone()[0] is not None
