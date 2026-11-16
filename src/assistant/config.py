"""Load settings from config.toml.

tomllib ships with Python 3.11+, so no extra dependency is needed.
We validate early: a missing setting should fail at startup with a clear
message, not halfway through a sync.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .costs import PRICES, PRICES_AS_OF, Caps, digest_worst_usd, known_model
from .errors import ConfigError

EFFORTS = ("low", "medium", "high", "xhigh", "max")
LLM_KEYS = {"extract_model", "digest_model", "digest_effort", "monthly_soft_cap_usd", "daily_cap_usd"}
EXTRACT_MODELS = ("claude-haiku-5-5",)
ENGINE_KEYS = {"extract_workers"}


@dataclass(frozen=True)
class LlmConfig:
    """Models and money. Defaults are the approved plan (D1, D4): Haiku 5.5
    extracts, Opus 5.5 at low effort writes the digest, $5/month soft cap
    (above it only extraction runs), $1/day hard stop."""
    extract_model: str = "claude-haiku-5-5"
    digest_model: str = "claude-opus-5-5"
    digest_effort: str = "low"
    caps: Caps = field(default_factory=Caps)


@dataclass(frozen=True)
class EngineConfig:
    """The long-running engine. Four workers extract a backlog about four times
    faster; each worker is one model call in flight, so the caps are unchanged."""
    extract_workers: int = 4


@dataclass(frozen=True)
class Config:
    gmail_query: str
    max_messages: int
    university_addresses: tuple[str, ...]
    db_path: Path
    max_body_chars: int
    profile_path: Path = Path("profile.toml")
    llm: LlmConfig = field(default_factory=LlmConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    gmail_address: str = ""  # lower case; empty until set: `stats` and `list` work without it


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
        gmail_query=gmail.get("query", "newer_than:{days}d -in:chats -in:spam -in:trash"),
        max_messages=int(gmail.get("max_messages", 500)),
        # Lower-case once here so every comparison later is case-insensitive
        university_addresses=tuple(a.lower() for a in uni.get("addresses", [])),
        db_path=base / storage.get("db_path", "assistant.db"),
        max_body_chars=int(storage.get("max_body_chars", 4000)),
        # Next to config.toml by default, so a scheduled task (which starts in
        # System32) still finds it
        profile_path=base / paths.get("profile", "profile.toml"),
        llm=_llm(raw.get("llm", {})),
        engine=_engine(raw.get("engine", {})),
        gmail_address=_gmail_address(gmail.get("address", "")),
    )


# Plain ASCII, no spaces or line breaks: IMAP's LOGIN sends the address unquoted
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


def _gmail_address(value) -> str:
    if not isinstance(value, str) or (value.strip() and not ADDRESS.fullmatch(value.strip())):
        raise ConfigError('[gmail] address must be your Gmail address, e.g. "you@gmail.com"')
    return value.strip().lower()


def _engine(section: dict) -> EngineConfig:
    if not isinstance(section, dict):
        raise ConfigError("[engine] must be a section")
    unknown = set(section) - ENGINE_KEYS
    if unknown:
        raise ConfigError(f"[engine] has unknown key(s) {sorted(unknown)}; expected {sorted(ENGINE_KEYS)}")
    workers = section.get("extract_workers", EngineConfig.extract_workers)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ConfigError("[engine] extract_workers must be a whole number from 1 to 8, e.g. 4")
    return EngineConfig(extract_workers=workers)


def _llm(section: dict) -> LlmConfig:
    if not isinstance(section, dict):
        raise ConfigError("[llm] must be a section")
    unknown = set(section) - LLM_KEYS
    if unknown:
        raise ConfigError(f"[llm] has unknown key(s) {sorted(unknown)}; expected {sorted(LLM_KEYS)}")
    defaults = LlmConfig()
    effort = section.get("digest_effort", defaults.digest_effort)
    if effort not in EFFORTS:
        raise ConfigError(f'[llm] digest_effort must be one of {list(EFFORTS)}, not "{effort}"')
    caps = {}
    for key, default in (("monthly_soft_cap_usd", defaults.caps.monthly_soft_usd),
                         ("daily_cap_usd", defaults.caps.daily_usd)):
        value = section.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
            raise ConfigError(f"[llm] {key} must be a number of dollars between 0 and 100, e.g. {default}")
        caps[key] = float(value)
    models = {}
    for key in ("extract_model", "digest_model"):
        value = section.get(key, getattr(defaults, key))
        if not isinstance(value, str) or not value.startswith("claude-"):
            raise ConfigError(f'[llm] {key} must be a Claude model id, e.g. "{getattr(defaults, key)}"')
        if not known_model(value):
            # It would be priced at the most expensive rate and trip the caps
            # for no visible reason: add its real price to the table first.
            raise ConfigError(
                f'[llm] {key} "{value}" has no price in src/assistant/costs.py (prices as of {PRICES_AS_OF}). '
                f"Add its price there first, or use one of {sorted(PRICES)}"
            )
        models[key] = value
    if models["extract_model"] not in EXTRACT_MODELS:
        # The request (thinking off at effort low) returns a 400 on Opus 5.5 and
        # Sonnet 5.5, and Haiku 4.5 has no effort setting at all
        raise ConfigError(
            f'[llm] extract_model must be one of {list(EXTRACT_MODELS)}: the extraction request was '
            f'checked only for those, and other models reject it. Not "{models["extract_model"]}".'
        )
    # Rounded UP to the cent, so the figure in the message is itself a valid cap
    one_digest = math.ceil(digest_worst_usd(models["digest_model"]) * 100) / 100
    for key, value in caps.items():
        if value < one_digest:
            raise ConfigError(
                f"[llm] {key} = {value} is below one digest's worst case (${one_digest:.2f} with "
                f"{models['digest_model']}), so the digest would never run. Raise it, or pick a cheaper digest_model."
            )
    return LlmConfig(
        digest_effort=effort,
        caps=Caps(monthly_soft_usd=caps["monthly_soft_cap_usd"], daily_usd=caps["daily_cap_usd"]),
        **models,
    )
