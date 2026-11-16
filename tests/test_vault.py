"""API keys: stored in the (fake) keychain, never echoed, never read from the environment.

Run: pytest
"""

import io

import pytest

from assistant.vault import load, redact, save
from assistant.cli import main, set_key
from assistant.errors import SecretError

FAKE_KEY = "sk-ant-api03-" + "Zx9_-" * 18 + "wxyz"  # the shape of a real key, not a real key


def test_a_pasted_key_is_trimmed_stored_and_confirmed_by_its_last_characters(vault):
    assert save("anthropic", f"  {FAKE_KEY}\r\n") == "wxyz"
    assert load("anthropic") == FAKE_KEY
    assert vault == {("pocket-assistant", "anthropic-api-key"): FAKE_KEY}  # one entry, beside the Gmail token


def test_a_personal_key_is_accepted(vault):
    # The Console's personal keys (Linked account: yourself) start sk-ant-usr-, with no version number
    personal = "sk-ant-usr-" + "Qw7_-" * 20 + "AbCd"
    assert save("anthropic", personal) == "AbCd"
    assert vault == {("pocket-assistant", "anthropic-api-key"): personal}


@pytest.mark.parametrize("pasted", [
    "hunter2-my-password", "", "sk-ant-short", f"{FAKE_KEY} extra",
    "sk-ant-" + "-" * 60,                # starts right, but isn't an API key
])
def test_something_that_is_not_a_key_is_refused_and_never_echoed(pasted, vault):
    with pytest.raises(SecretError) as refused:
        save("anthropic", pasted)
    assert "Nothing was saved" in str(refused.value)
    if pasted:
        assert pasted not in str(refused.value)
    assert vault == {}


@pytest.mark.parametrize("pasted, named", [
    ("sk-ant-admin01-" + "a" * 90, "Admin API key"),   # would control the whole organization
    ("sk-ant-oat01-" + "a" * 90, "OAuth token"),
])
def test_keys_that_could_do_far_more_than_this_app_are_refused(pasted, named, vault):
    with pytest.raises(SecretError, match=named) as refused:
        save("anthropic", pasted)
    assert pasted not in str(refused.value) and vault == {}


def test_the_key_comes_from_credential_manager_never_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    with pytest.raises(SecretError, match="assistant set-key anthropic"):
        load("anthropic")


def test_redact_hides_anything_shaped_like_a_key():
    text = f"401 from the API: invalid x-api-key {FAKE_KEY}, request req_1"
    cleaned = redact(text)
    assert FAKE_KEY not in cleaned and "wxyz" not in cleaned
    assert cleaned == "401 from the API: invalid x-api-key sk-ant-[redacted], request req_1"
    # a key cut off mid-way (say by a length limit) is still hidden
    assert redact("...key sk-ant-api03-SECRET") == "...key sk-ant-[redacted]"


def test_set_key_prompts_without_echo_and_prints_only_the_hint(capsys):
    prompts = []
    set_key("anthropic", ask=lambda prompt: prompts.append(prompt) or FAKE_KEY)
    out = capsys.readouterr().out
    assert "Nothing shows while you paste or type" in prompts[0]
    assert "ends in ...wxyz" in out and FAKE_KEY not in out


class Piped(io.StringIO):
    def isatty(self):
        return False


class Typed(io.StringIO):
    def isatty(self):
        return True


def test_a_key_piped_from_the_clipboard_is_saved(vault, capsys):
    # What `Get-Clipboard | assistant set-key anthropic --stdin` delivers on Windows PowerShell 5.1:
    # a byte-order mark first and a newline last (checked on this PC)
    set_key("anthropic", from_stdin=True, stdin=Piped(f"﻿{FAKE_KEY}\n"))
    assert vault == {("pocket-assistant", "anthropic-api-key"): FAKE_KEY}
    out = capsys.readouterr().out
    assert "ends in ...wxyz" in out and FAKE_KEY not in out


def test_stdin_mode_refuses_typing_because_the_key_would_show():
    with pytest.raises(SecretError, match="Get-Clipboard"):
        set_key("anthropic", from_stdin=True, stdin=Typed(""))


def test_set_key_command_refuses_a_bad_paste_cleanly(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "my-gmail-password")
    with pytest.raises(SystemExit) as exited:
        main(["--config", str(tmp_path / "config.toml"), "set-key", "anthropic"])
    assert "doesn't look like an Anthropic API key" in str(exited.value.code)
    assert "my-gmail-password" not in str(exited.value.code)


# --- the Gmail App Password --------------------------------------------------------------------

@pytest.mark.parametrize("pasted", ["abcdefghijklmnop", "abcd efgh ijkl mnop", " abcd efgh ijkl mnop\n",
                                    "\ufeffabcd efgh ijkl mnop"])
def test_a_gmail_password_is_accepted_as_google_shows_it(pasted, vault):
    save("gmail", pasted)
    assert load("gmail") == "abcdefghijklmnop"


@pytest.mark.parametrize("wrong", ["short", "abcdefghijklmnopq", "ABCDEFGHIJKLMNOP", "abcd1234efgh5678"])
def test_a_wrong_gmail_password_is_refused_without_echoing_it(wrong, vault):
    with pytest.raises(SecretError) as info:
        save("gmail", wrong)
    assert wrong not in str(info.value) and "16 letters" in str(info.value)


def test_set_key_gmail_prints_no_part_of_the_password(vault, capsys):
    set_key("gmail", ask=lambda prompt: "abcd efgh ijkl mnop")
    out = capsys.readouterr().out
    assert "Saved" in out and "mnop" not in out and "ends in" not in out


def test_the_cli_offers_gmail(vault, tmp_path, capsys):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as info:
        main(["--config", str(tmp_path / "config.toml"), "set-key", "nonsense"])
    assert info.value.code == 2
    assert "gmail" in capsys.readouterr().err


def test_anthropic_keys_still_cant_hold_spaces(vault):
    with pytest.raises(SecretError):
        save("anthropic", FAKE_KEY[:30] + " " + FAKE_KEY[30:])
