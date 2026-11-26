"""API keys in Windows Credential Manager (via keyring), or in a private secrets.json
on other systems (file_keyring.py), never in env vars.

Why not ANTHROPIC_API_KEY in the environment, the SDK's default? Every
program this one starts inherits environment variables, and they end up in
.env files and shell history that get committed or shared. Credential
Manager encrypts the key under your Windows login, next to the Gmail token.

Error messages never echo what was pasted: if you paste the wrong thing
(say, a password), it must not be printed back or logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import keyring

from .errors import SecretError

KEYRING_SERVICE = "pocket-assistant"
OLD_GOOGLE_TOKEN = "gmail-oauth-token"  # the sign-in from before the App Password


@dataclass(frozen=True)
class SecretKind:
    entry: str                       # the keyring entry name
    label: str
    shape: re.Pattern[str]           # what a real one looks like
    wrong_kinds: dict[str, str]      # prefix -> what it is instead, for a clearer refusal
    looks_like: str                  # finishes "it should ...", for the refusal
    where: str
    spaces_allowed: bool = False     # Google shows an App Password in groups of four
    shows_ending: bool = True        # False: not even the last characters are ever printed


SECRETS = {
    "anthropic": SecretKind(
        entry="anthropic-api-key",
        label="Anthropic API key",
        # sk-ant-usr-: a personal key (linked to you); sk-ant-api03-: a workspace key
        shape=re.compile(r"sk-ant-(?:usr|api\d\d)-[A-Za-z0-9_-]{40,300}"),
        wrong_kinds={
            # Both would work far beyond one $10 workspace: never store them here
            "sk-ant-admin": "an Admin API key, which can manage the whole organization",
            "sk-ant-o": "an OAuth token, not an API key",
        },
        looks_like="start with sk-ant-usr- (a personal key) or sk-ant-api03-",
        where="Claude Console -> API keys, in the pocket-assistant workspace",
    ),
    "gmail": SecretKind(
        entry="gmail-app-password",
        label="Gmail App Password",
        shape=re.compile(r"[a-z]{16}"),
        wrong_kinds={},
        looks_like="be 16 letters",
        where="https://myaccount.google.com/apppasswords",
        spaces_allowed=True,
        shows_ending=False,
    ),
}
# Anything that starts like any Anthropic secret, however short: redact errs wide
REDACT = re.compile(r"sk-ant-[A-Za-z0-9_-]*")


def save(kind: str, value: str) -> str:
    """Check the shape and store it. Returns the last 4 characters, so you can
    match it against the key hint the Console shows, or "" for a kind that never shows any."""
    spec = SECRETS[kind]
    value = check(kind, value)
    keyring.set_password(KEYRING_SERVICE, spec.entry, value)
    return value[-4:] if spec.shows_ending else ""


def check(kind: str, value: str) -> str:
    """The value as it would be stored, or SecretError if it doesn't look right. Stores nothing."""
    spec = SECRETS[kind]
    # Pasting often adds a newline or spaces, and a PowerShell pipe adds an
    # invisible byte-order mark (U+FEFF) in front
    value = value.strip().lstrip("﻿").strip()
    if spec.spaces_allowed:
        value = "".join(value.split())
    for prefix, what in spec.wrong_kinds.items():
        if value.startswith(prefix):
            raise SecretError(f"That is {what}. Nothing was saved. Create a normal API key in {spec.where}.")
    if not spec.shape.fullmatch(value):
        raise SecretError(
            f"That doesn't look like {'an' if spec.label[0] in 'AEIOU' else 'a'} {spec.label} (it should {spec.looks_like}). "
            f"Nothing was saved. Copy it again from {spec.where}."
        )
    return value


def load(kind: str) -> str:
    spec = SECRETS[kind]
    value = keyring.get_password(KEYRING_SERVICE, spec.entry)
    if not value:
        raise SecretError(f"No {spec.label} saved yet. Run: assistant set-key {kind}")
    return value


def remove_old_google_token() -> bool:
    """True when there was one to remove."""
    if keyring.get_password(KEYRING_SERVICE, OLD_GOOGLE_TOKEN) is None:
        return False
    keyring.delete_password(KEYRING_SERVICE, OLD_GOOGLE_TOKEN)
    return True


def redact(text: str) -> str:
    """Hide anything shaped like a key, even a cut-off one, for messages and logs."""
    return REDACT.sub("sk-ant-[redacted]", text)
