"""LLM cost arithmetic and spending caps. Pure: no network, no database.

Why a price table in code? So every price change is a reviewed, dated
commit, and an old config.toml can never silently price calls at $0. The
table was checked against the live pricing page on PRICES_AS_OF; the
Anthropic Console workspace limit ($10/month) is the backstop if it drifts.

Two rules keep the cap honest:
- "Fail expensive, never free": a model missing from the table is priced
  like the most expensive model, so a typo can't make spending invisible.
- A call is recorded BEFORE it is made, at its worst-case cost, then
  corrected with the real usage. A crash or timeout mid-call leaves an
  over-estimate in the ledger, never a gap.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

PRICES_AS_OF = "2026-10-15"  # platform.claude.com/docs/en/about-claude/pricing
TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")  # months and days roll over at local midnight


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float

    @property
    def dearest_input(self) -> float:
        """The most a prompt token can cost: today a 1-hour cache write (2x input)."""
        return max(self.input, self.cache_read, self.cache_write_5m, self.cache_write_1h)


@dataclass(frozen=True)
class ModelPrices:
    standard: Price
    long: Price | None = None         # Haiku 5.5: prompts over long_threshold tokens
    long_threshold: int | None = None

    def for_prompt(self, prompt_tokens: int) -> Price:
        if self.long and self.long_threshold is not None and prompt_tokens > self.long_threshold:
            return self.long
        return self.standard


PRICES: dict[str, ModelPrices] = {
    "claude-haiku-5-5": ModelPrices(
        standard=Price(0.10, 0.50, 0.01, 0.125, 0.20),
        long=Price(0.50, 2.50, 0.05, 0.625, 1.00),
        long_threshold=100_000,
    ),
    "claude-sonnet-5-5": ModelPrices(Price(2.00, 10.00, 0.10, 2.50, 4.00)),
    "claude-opus-5-5": ModelPrices(Price(4.00, 20.00, 0.20, 5.00, 8.00)),
    # Possible server-side fallbacks for an Opus 5.5 refusal, and older models
    "claude-opus-5": ModelPrices(Price(5.00, 25.00, 0.50, 6.25, 10.00)),
    "claude-opus-4-8": ModelPrices(Price(5.00, 25.00, 0.50, 6.25, 10.00)),
    "claude-sonnet-5": ModelPrices(Price(2.00, 10.00, 0.20, 2.50, 4.00)),
    "claude-haiku-4-5": ModelPrices(Price(1.00, 5.00, 0.10, 1.25, 2.00)),
}
# Unknown models: priced like the most expensive model the first-party API
# still serves (Fable 5.x). Retired models listed higher can't be billed.
EXPENSIVE = ModelPrices(Price(10.00, 50.00, 1.00, 12.50, 20.00))
# A refused Opus 5.5 call may be re-run on another model (today Opus 5 or 4.8).
# Budget that attempt at EXPENSIVE, the price the ledger records for a model
# missing from the table, so the estimate is never below what can be recorded.
FALLBACK_WORST = EXPENSIVE

# Who may spend past the monthly soft cap. Only extraction: without it, new
# mail piles up unread. The digest falls back to the free template instead.
PURPOSES = ("extract", "digest", "smoke", "record")
ESSENTIAL_PURPOSES = frozenset({"extract"})

# The digest's token budget (plan section 5). Step 11 sends at most this much,
# and config.py refuses caps too small for even one digest.
DIGEST_PROMPT_TOKENS = 4_000
DIGEST_MAX_TOKENS = 8_000

_SNAPSHOT_DATE = re.compile(r"-\d{8}$")  # claude-haiku-4-5-20251001 -> claude-haiku-4-5


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0            # already INCLUDES thinking tokens
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    thinking_tokens: int = 0          # for information only: a subset of output_tokens

    def __post_init__(self) -> None:
        # A badly mapped response must fail here: a negative count would lower
        # the recorded spend and unblock the caps; an infinite one would jam them.
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Usage.{f.name} must be a whole number >= 0, not {value!r}")
        if self.thinking_tokens > self.output_tokens:
            raise ValueError("thinking_tokens is part of output_tokens, so it can't be larger")

    @property
    def prompt_tokens(self) -> int:
        """What the long-context tier counts: every input token, cached or not."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_5m_tokens + self.cache_write_1h_tokens


def known_model(model: str) -> str | None:
    """The price-table key for a model id (a dated snapshot id included), or None."""
    key = _SNAPSHOT_DATE.sub("", model)
    return key if key in PRICES else None


def prices_for(model: str) -> ModelPrices:
    key = known_model(model)
    return PRICES[key] if key else EXPENSIVE


def cost_usd(model: str, usage: Usage) -> float:
    p = prices_for(model).for_prompt(usage.prompt_tokens)
    dollars_per_million = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output          # thinking is inside output: never added twice
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_5m_tokens * p.cache_write_5m
        + usage.cache_write_1h_tokens * p.cache_write_1h
    )
    return dollars_per_million / 1_000_000


def attempts_cost(attempts: list[tuple[str, Usage]]) -> float:
    """A call that fell back to another model bills each attempt at its own model's prices."""
    return sum(cost_usd(model, usage) for model, usage in attempts)


def attempts_from_usage(usage: Mapping[str, Any], *, served_model: str, requested_model: str) -> list[tuple[str, Usage]]:
    """A response's `usage` (as a dict) -> the attempts it billed, each with its model.

    Rules, checked against the SDK's types and the docs on 2026-10-16:
    - `usage.iterations`, when present, is the per-attempt billing record: the
      top-level numbers cover only the attempt that answered. One attempt per
      entry, at the entry's model (a plain "message" entry may leave it out:
      that is the requested model).
    - A field the API leaves out or sends as null counts as 0.
    - Cache writes are split by `cache_creation`; any write the split doesn't
      account for is priced at the 1-hour rate (fail expensive).
    - Thinking is reported once, at the top level, for the attempt that answered.
      It is part of output, never extra cost, so it is clamped to that output.
    """
    def count(data: Mapping[str, Any] | None, key: str) -> int:
        return int((data or {}).get(key) or 0)

    def one(entry: Mapping[str, Any]) -> Usage:
        split = entry.get("cache_creation") or {}
        write_5m = count(split, "ephemeral_5m_input_tokens")
        write_1h = count(split, "ephemeral_1h_input_tokens")
        unreported = max(count(entry, "cache_creation_input_tokens") - write_5m - write_1h, 0)
        return Usage(
            input_tokens=count(entry, "input_tokens"),
            output_tokens=count(entry, "output_tokens"),
            cache_read_tokens=count(entry, "cache_read_input_tokens"),
            cache_write_5m_tokens=write_5m,
            cache_write_1h_tokens=write_1h + unreported,
        )

    iterations = usage.get("iterations") or []
    if iterations:
        attempts = [(entry.get("model") or requested_model, one(entry)) for entry in iterations]
    else:
        attempts = [(served_model, one(usage))]
    model, last = attempts[-1]
    thinking = min(count(usage.get("output_tokens_details"), "thinking_tokens"), last.output_tokens)
    attempts[-1] = (model, replace(last, thinking_tokens=thinking))
    return attempts


def total_usage(attempts: list[tuple[str, Usage]]) -> Usage:
    """Token counts summed over every attempt (for the ledger's columns, never for pricing)."""
    return Usage(*(sum(getattr(u, f.name) for _, u in attempts) for f in fields(Usage)))


def estimate_usd(model: str, prompt_tokens: int, max_tokens: int, fallback: bool = False) -> float:
    """Worst case before the call, an upper bound on what it can bill.

    prompt_tokens is the WHOLE prompt (system, messages, tool definitions),
    cached or not, and must itself be an upper bound: an under-count can also
    hide Haiku's 5x long-prompt tier. Every output token is used (thinking
    counts toward max_tokens), and every prompt token is priced at its dearest
    rate: a prompt written to the cache costs more than a plain one. With
    server-side fallbacks, a refusal can bill a full second attempt.
    """
    def worst(prices: ModelPrices) -> float:
        p = prices.for_prompt(prompt_tokens)
        return (prompt_tokens * p.dearest_input + max_tokens * p.output) / 1_000_000

    return worst(prices_for(model)) + (worst(FALLBACK_WORST) if fallback else 0.0)


def digest_worst_usd(model: str) -> float:
    """One digest at its full token budget, including a fallback attempt."""
    return estimate_usd(model, DIGEST_PROMPT_TOKENS, DIGEST_MAX_TOKENS, fallback=True)


def local_period(now_utc: datetime) -> tuple[str, str]:
    """('2026-11', '2026-11-01') for 2026-10-31T17:30Z: periods follow HCMC midnight.

    A naive datetime is refused: astimezone() would read it as the PC's
    local time, and datetime.utcnow() would land 7 hours in the past.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware, e.g. datetime.now(timezone.utc)")
    local = now_utc.astimezone(TIMEZONE)
    return local.strftime("%Y-%m"), local.strftime("%Y-%m-%d")


def is_essential(purpose: str) -> bool:
    if purpose not in PURPOSES:
        raise ValueError(f"unknown LLM call purpose {purpose!r}; expected one of {PURPOSES}")
    return purpose in ESSENTIAL_PURPOSES


def check_dollars(name: str, value: float) -> None:
    """Ledger amounts must be real, non-negative numbers: a negative one would unblock spending."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number of dollars, not {value!r}")


@dataclass(frozen=True)
class Caps:
    monthly_soft_usd: float = 5.0   # above it: only essential calls (extraction) still run
    daily_usd: float = 1.0          # above it: nothing runs until tomorrow (stops runaway loops)


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str | None = None


def allowed(purpose: str, month_spent: float, day_spent: float, estimate: float, caps: Caps) -> Decision:
    """May a call with this worst-case cost go ahead?"""
    essential = is_essential(purpose)  # first, so a typo'd purpose fails even when capped
    if day_spent + estimate > caps.daily_usd:
        return Decision(False, "daily-cap")
    if not essential and month_spent + estimate > caps.monthly_soft_usd:
        return Decision(False, "monthly-cap")
    return Decision(True)
