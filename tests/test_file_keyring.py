"""The file-backed keyring that stands in for Credential Manager on a headless Linux box."""

import io
import json
import os
import sys

import keyring
import keyring.errors
import pytest

from assistant.cli import main
from assistant.config import load_config
from assistant.errors import AssistantError
from assistant.file_keyring import FileKeyring, install_for

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
FAKE_KEY = "sk-ant-api03-" + "Zx9_-" * 18 + "wxyz"


@pytest.fixture(autouse=True)
def restore_keyring():
    before = keyring.get_keyring()
    yield
    keyring.set_keyring(before)


@posix
def test_round_trip_and_the_file_is_private(tmp_path):
    ring = FileKeyring(tmp_path / "secrets.json")
    ring.set_password("pocket-assistant", "gmail-app-password", "abcdabcdabcdabcd")
    assert ring.get_password("pocket-assistant", "gmail-app-password") == "abcdabcdabcdabcd"
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600


@posix
def test_a_file_others_can_read_is_refused_with_the_fix(tmp_path):
    path = tmp_path / "secrets.json"
    FileKeyring(path).set_password("s", "u", "the-secret")
    path.chmod(0o644)
    with pytest.raises(AssistantError) as refused:
        FileKeyring(path).get_password("s", "u")
    assert f"chmod 600 {path}" in str(refused.value) and "the-secret" not in str(refused.value)


@posix
def test_an_unreadable_or_unwritable_secrets_file_is_a_message_not_a_traceback(tmp_path):
    path = tmp_path / "secrets.json"
    ring = FileKeyring(path)
    ring.set_password("s", "u", "the-secret")
    path.chmod(0o000)
    try:
        with pytest.raises(AssistantError) as unreadable:
            ring.get_password("s", "u")
        assert f"chown pocket:pocket {path}" in str(unreadable.value) and "the-secret" not in str(unreadable.value)
    finally:
        path.chmod(0o600)
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(AssistantError) as unwritable:
            ring.set_password("s", "u2", "other")
        assert f"chown pocket:pocket {tmp_path}" in str(unwritable.value)
    finally:
        tmp_path.chmod(0o700)


@posix
def test_a_failed_write_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    ring = FileKeyring(tmp_path / "secrets.json")
    ring.set_password("s", "u", "old")

    def boom(*_):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        ring.set_password("s", "u", "new")
    assert ring.get_password("s", "u") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]


def test_a_missing_file_reads_as_none(tmp_path):
    assert FileKeyring(tmp_path / "secrets.json").get_password("s", "u") is None


@posix
def test_delete_removes_only_that_entry_and_a_missing_one_raises(tmp_path):
    path = tmp_path / "secrets.json"
    ring = FileKeyring(path)
    ring.set_password("s", "a", "1")
    ring.set_password("s", "b", "2")
    ring.delete_password("s", "a")
    assert json.loads(path.read_text()) == {"s": {"b": "2"}}
    with pytest.raises(keyring.errors.PasswordDeleteError):
        ring.delete_password("s", "a")


def _cfg(tmp_path):
    (tmp_path / "config.toml").write_text('[gmail]\naddress = "me@gmail.com"\n', encoding="utf-8")
    return tmp_path / "config.toml"


def test_install_is_a_no_op_on_windows_and_installs_elsewhere(tmp_path, monkeypatch):
    cfg = load_config(_cfg(tmp_path))
    before = keyring.get_keyring()
    monkeypatch.setattr(sys, "platform", "win32")
    install_for(cfg)
    assert keyring.get_keyring() is before
    monkeypatch.setattr(sys, "platform", "linux")
    install_for(cfg)
    assert isinstance(keyring.get_keyring(), FileKeyring)
    assert keyring.get_keyring().path == cfg.db_path.parent / "secrets.json"


@posix
def test_set_key_on_linux_saves_into_the_file(tmp_path, monkeypatch):
    config = _cfg(tmp_path)
    for name in ("set_password", "get_password", "delete_password"):  # undo conftest's in-memory vault
        monkeypatch.setattr(keyring, name, getattr(keyring.core, name))
    monkeypatch.setattr("sys.stdin", io.StringIO(FAKE_KEY + "\n"))
    main(["--config", str(config), "set-key", "anthropic", "--stdin"])
    assert json.loads((tmp_path / "secrets.json").read_text())["pocket-assistant"]["anthropic-api-key"] == FAKE_KEY


def test_set_key_names_where_the_key_went(tmp_path, capsys, monkeypatch):
    from assistant.cli import set_key

    set_key("anthropic", ask=lambda _: FAKE_KEY)
    assert "Saved to Windows Credential Manager." in capsys.readouterr().out  # conftest keeps the default backend
    keyring.set_keyring(FileKeyring(tmp_path / "secrets.json"))
    set_key("anthropic", ask=lambda _: FAKE_KEY)
    out = capsys.readouterr().out
    assert f"Saved to the private secrets file {tmp_path / 'secrets.json'}." in out
    assert "Credential Manager" not in out
