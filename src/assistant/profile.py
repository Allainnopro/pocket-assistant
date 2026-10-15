"""profile.toml: what the radar looks for. Personal, so it is git-ignored;
profile.example.toml shows the shape.

Loaded and validated up front, like config.py. Why so strict? A sign
mistake (a penalty written as +30) would silently push the very roles you
don't want UP the ranking; a typo'd key (outside_hom) would silently be
ignored; drop_subject_patterns = "weekly digest" (no brackets) would split
into single letters and drop EVERY email. All of these fail loudly at
startup instead, with a message saying how to fix them.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .models import OPPORTUNITY_KINDS
from .textnorm import fold

REMOTE_SCOPES = ("anywhere", "vietnam", "no")
MAX_WEIGHT = 100
# Allowed keys per section; [boost], [penalize] and [aliases] take any keyword.
KEYS = {
    "me": {"level", "interests", "open_to"},
    "location": {"home", "outside_home", "remote_ok"},
    "filter": {"drop_sender_domains", "drop_subject_patterns", "drop_promotions"},
    "boost": None,
    "penalize": None,
    "aliases": None,
}


@dataclass(frozen=True)
class Profile:
    interests: tuple[str, ...]       # phrases for the model's relevance judgement
    open_to: tuple[str, ...]         # kinds worth showing (OPPORTUNITY_KINDS)
    level: str                       # "student", "intern", "fresher", ...
    home: str                        # home city keyword, e.g. "HCMC" (spellings in aliases)
    outside_home: int                # weight (<= 0) for work outside home, incl. remote you can't take
    remote_ok: str                   # "anywhere" | "vietnam" | "no"
    boost: dict[str, int]            # keyword -> weight > 0
    penalize: dict[str, int]         # keyword -> weight < 0
    aliases: dict[str, tuple[str, ...]]  # keyword -> other spellings (incl. Vietnamese)
    drop_sender_domains: tuple[str, ...] = ()
    drop_subject_patterns: tuple[str, ...] = ()
    drop_promotions: bool = False    # Gmail "Promotions" tab; off: it held a real game-jam invite

    def spellings(self, keyword: str) -> tuple[str, ...]:
        """The keyword itself plus its aliases, for matching."""
        return (keyword, *self.aliases.get(keyword, ()))


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    raw = _read_toml(path)

    for section, value in raw.items():
        if section not in KEYS:
            raise ConfigError(f"{path.name}: unknown section [{section}]; expected one of {sorted(KEYS)}")
        if not isinstance(value, dict):
            raise ConfigError(f"{path.name}: [{section}] must be a section (a [{section}] header), not a value")
        allowed = KEYS[section]
        if allowed is not None and set(value) - allowed:
            raise ConfigError(
                f"[{section}] has unknown key(s) {sorted(set(value) - allowed)}; expected {sorted(allowed)}"
            )

    me, location, filt = raw.get("me", {}), raw.get("location", {}), raw.get("filter", {})
    boost = _weights(raw.get("boost", {}), "boost", positive=True)
    penalize = _weights(raw.get("penalize", {}), "penalize", positive=False)
    clash = {fold(k) for k in boost} & {fold(k) for k in penalize}
    if clash:
        raise ConfigError(f"{sorted(clash)} appear in both [boost] and [penalize]; keep each keyword in one")

    open_to = _str_list(me.get("open_to", list(OPPORTUNITY_KINDS)), "[me] open_to", '["hackathon", "internship"]')
    bad_kinds = [k for k in open_to if k not in OPPORTUNITY_KINDS]
    if bad_kinds:
        raise ConfigError(f"[me] open_to has unknown kinds {bad_kinds}; choose from {list(OPPORTUNITY_KINDS)}")

    remote_ok = _str(location.get("remote_ok", "anywhere"), "[location] remote_ok")
    if remote_ok not in REMOTE_SCOPES:
        raise ConfigError(f'[location] remote_ok must be one of {list(REMOTE_SCOPES)}, not "{remote_ok}"')
    outside_home = location.get("outside_home", 0)
    if not isinstance(outside_home, int) or isinstance(outside_home, bool) or not -MAX_WEIGHT <= outside_home <= 0:
        raise ConfigError(f"[location] outside_home must be a whole number between -{MAX_WEIGHT} and 0, e.g. -30")
    home = _str(location.get("home", ""), "[location] home")

    aliases = {k: _str_list(v, f'[aliases] "{k}"', '["Sài Gòn", "TP.HCM"]')
               for k, v in raw.get("aliases", {}).items()}
    known = set(boost) | set(penalize) | ({home} if home else set())
    stray = [k for k in aliases if k not in known]
    if stray:
        raise ConfigError(f"[aliases] {stray} match no keyword in [boost], [penalize] or [location] home")

    drop_promotions = filt.get("drop_promotions", False)
    if not isinstance(drop_promotions, bool):
        raise ConfigError("[filter] drop_promotions must be true or false (without quotes)")
    patterns = _str_list(filt.get("drop_subject_patterns", []), "[filter] drop_subject_patterns", '["weekly digest"]')
    short = [p for p in patterns if len(p) < 3]
    if short:
        raise ConfigError(f"[filter] drop_subject_patterns entries must be at least 3 characters: {short}")

    return Profile(
        interests=_str_list(me.get("interests", []), "[me] interests", '["machine learning", "LLM apps"]'),
        open_to=open_to,
        level=_str(me.get("level", "student"), "[me] level"),
        home=home,
        outside_home=outside_home,
        remote_ok=remote_ok,
        boost=boost,
        penalize=penalize,
        aliases=aliases,
        drop_sender_domains=_domains(filt.get("drop_sender_domains", [])),
        drop_subject_patterns=patterns,
        drop_promotions=drop_promotions,
    )


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(
            f"Profile not found: {path}\n"
            "Copy profile.example.toml to profile.toml and fill in your interests."
        )
    data = path.read_bytes().removeprefix(b"\xef\xbb\xbf")  # Notepad's "UTF-8 with BOM"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path.name} is not saved as UTF-8. In Notepad: File > Save As > Encoding: UTF-8"
        ) from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc


def _str(value, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f'{where} must be text in quotes, e.g. "{where.split()[-1]}"')
    return value.strip()


def _str_list(value, where: str, example: str) -> tuple[str, ...]:
    """A list of non-empty strings. A bare string is the classic mistake: it
    would otherwise be split into single characters."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of texts in square brackets, e.g. {example}")
    items = tuple(v.strip() for v in value)
    if any(not v for v in items):
        raise ConfigError(f"{where} has an empty entry; remove it")
    return items


def _domains(value) -> tuple[str, ...]:
    out = []
    for entry in _str_list(value, "[filter] drop_sender_domains", '["discord.com"]'):
        domain = entry.lower().rsplit("@", 1)[-1]  # "noreply@discord.com" -> "discord.com"
        if "." not in domain:
            raise ConfigError(f'[filter] drop_sender_domains: "{entry}" is not a domain like "discord.com"')
        out.append(domain)
    return tuple(out)


def _weights(table: dict, section: str, positive: bool) -> dict[str, int]:
    out: dict[str, int] = {}
    seen: dict[str, str] = {}
    for keyword, weight in table.items():
        if not keyword.strip():
            raise ConfigError(f"[{section}] has an empty keyword")
        if fold(keyword) in seen:
            raise ConfigError(f'[{section}] "{keyword}" and "{seen[fold(keyword)]}" are the same keyword; keep one')
        seen[fold(keyword)] = keyword
        if not isinstance(weight, int) or isinstance(weight, bool):
            raise ConfigError(f'[{section}] "{keyword}" must be a whole number, e.g. {"20" if positive else "-20"}')
        if positive and not 0 < weight <= MAX_WEIGHT:
            raise ConfigError(f'[boost] "{keyword}" = {weight}: boosts must be between 1 and {MAX_WEIGHT}')
        if not positive and not -MAX_WEIGHT <= weight < 0:
            raise ConfigError(
                f'[penalize] "{keyword}" = {weight}: penalties must be negative, e.g. "{keyword}" = -{abs(weight) or 20}'
            )
        out[keyword] = weight
    return out
