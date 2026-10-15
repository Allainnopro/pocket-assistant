"""Load settings from config.toml.

tomllib ships with Python 3.11+, so no extra dependency is needed.
We validate early: a missing setting should fail at startup with a clear
message, not halfway through a sync.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError


@dataclass(frozen=True)
class Config:
    credentials_file: Path
    gmail_query: str
    max_messages: int
    university_addresses: tuple[str, ...]
    db_path: Path
    max_body_chars: int
    profile_path: Path = Path("profile.toml")


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy config.example.toml to config.toml and edit it."
        )

    with path.open("rb") as f:  # tomllib requires binary mode
        raw = tomllib.load(f)

    base = path.parent  # relative paths in the config are relative to the config file
    gmail = raw.get("gmail", {})
    uni = raw.get("university", {})
    storage = raw.get("storage", {})
    paths = raw.get("paths", {})

    return Config(
        credentials_file=base / gmail.get("credentials_file", "credentials.json"),
        gmail_query=gmail.get("query", "newer_than:{days}d -in:chats -in:spam -in:trash"),
        max_messages=int(gmail.get("max_messages", 500)),
        # Lower-case once here so every comparison later is case-insensitive
        university_addresses=tuple(a.lower() for a in uni.get("addresses", [])),
        db_path=base / storage.get("db_path", "assistant.db"),
        max_body_chars=int(storage.get("max_body_chars", 4000)),
        # Next to config.toml by default, so a scheduled task (which starts in
        # System32) still finds it
        profile_path=base / paths.get("profile", "profile.toml"),
    )
