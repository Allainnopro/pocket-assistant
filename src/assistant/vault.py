"""API keys in Windows Credential Manager (via keyring), never in files or env vars.

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

KEYRING_SERVICE = "pocket-assistant"  # the same service the Gmail token uses


@dataclass(frozen=True)
class SecretKind:
    entry: str                       # the keyring entry name
    label: str
    shape: re.Pattern[str]           # what a real one looks like
    wrong_kinds: dict[str, str]      # prefix -> what it is instead, for a clearer refusal
    example: str
    where: str


SECRETS = {
    "anthropic": SecretKind(
        entry="anthropic-api-key",
        label="Anthropic API key",
        shape=re.compile(r"sk-ant-api\d\d-[A-Za-z0-9_-]{40,300}"),
        wrong_kinds={
            # Both would work far beyond one $10 workspace: never store them here
            "sk-ant-admin": "an Admin API key, which can manage the whole organization",
            "sk-ant-o": "an OAuth token, not an API key",
        },
        example="sk-ant-api03-...",
        where="Claude Console -> API keys, in the pocket-assistant workspace",
    ),
}
# Anything that starts like any Anthropic secret, however short: redact errs wide
REDACT = re.compile(r"sk-ant-[A-Za-z0-9_-]*")


def save(kind: str, value: str) -> str:
    """Check the shape and store it. Returns the last 4 characters, so you can
    match it against the key hint the Console shows."""
    spec = SECRETS[kind]
    value = value.strip()  # pasting often adds a newline or spaces
    for prefix, what in spec.wrong_kinds.items():
        if value.startswith(prefix):
            raise SecretError(f"That is {what}. Nothing was saved. Create a normal API key in {spec.where}.")
    if not spec.shape.fullmatch(value):
        raise SecretError(
            f"That doesn't look like an {spec.label} (it should start with {spec.example}). "
            f"Nothing was saved. Copy it again from {spec.where}."
        )
    keyring.set_password(KEYRING_SERVICE, spec.entry, value)
    return value[-4:]


def load(kind: str) -> str:
    spec = SECRETS[kind]
    value = keyring.get_password(KEYRING_SERVICE, spec.entry)
    if not value:
        raise SecretError(f"No {spec.label} saved yet. Run: assistant set-key {kind}")
    return value


def redact(text: str) -> str:
    """Hide anything shaped like a key, even a cut-off one, for messages and logs."""
    return REDACT.sub("sk-ant-[redacted]", text)
