"""The first screen (today), the reports and the settings.

Times: opportunities' first_seen_at is SQLite's datetime('now'), UTC. A day or a week
means one in Ho Chi Minh City, so its edges are turned into UTC before comparing.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..costs import TIMEZONE, local_period
from ..errors import SecretError
from ..rank import score
from .api_deps import Authorized, Ctx, Db, today_in_hcmc
from .api_opportunities import BEST_FIRST, FOR_YOU, OPEN, ROW_COLUMNS, OpportunityRow, row_model
from .events import RANKING_CHANGED

router = APIRouter()

UPCOMING_DAYS = 7      # today and the next 6
NEEDS_ACTION_DAYS = 7
TOP_PICKS = 5


def utc_text(day: date) -> str:
    """The UTC moment HCMC's `day` starts, as SQLite writes datetime('now')."""
    start = datetime.combine(day, time(), tzinfo=TIMEZONE).astimezone(timezone.utc)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def local_day(utc: str) -> date:
    return datetime.fromisoformat(utc).replace(tzinfo=timezone.utc).astimezone(TIMEZONE).date()


class Counts(BaseModel):
    new_today: int
    deadlines_this_week: int
    starred: int
    applied: int


class NeedsAction(BaseModel):
    message_id: str
    subject: str | None
    action_note: str | None
    received_at: str


class Spend(BaseModel):
    month_usd: float
    today_usd: float
    monthly_soft_cap_usd: float
    daily_cap_usd: float


class Status(BaseModel):
    last_ok_check_at: str | None
    last_check_error: str | None
    problems: dict[str, dict]


class Today(BaseModel):
    counts: Counts
    top_picks: list[OpportunityRow]
    needs_action: list[NeedsAction]
    upcoming_deadlines: list[OpportunityRow]
    spend: Spend
    status: Status


@router.get("/api/today")
def today(_: Authorized, ctx: Ctx, db: Db) -> Today:
    day = today_in_hcmc(ctx)
    params = {"today": day.isoformat(), "for_you": ctx.cfg.dashboard.for_you_min_score,
              "last_day": (day + timedelta(days=UPCOMING_DAYS - 1)).isoformat(),
              "start": utc_text(day), "end": utc_text(day + timedelta(days=1))}

    def count(condition: str) -> int:
        return db.conn.execute(f"SELECT COUNT(*) FROM opportunities WHERE {condition}", params).fetchone()[0]

    def rows(condition: str, order: str, limit: int = -1) -> list[OpportunityRow]:
        found = db.conn.execute(f"SELECT {ROW_COLUMNS} FROM opportunities WHERE {condition} ORDER BY {order} "
                                f"LIMIT {limit}", params).fetchall()
        return [row_model(r) for r in found]

    upcoming = f"{OPEN} AND deadline BETWEEN :today AND :last_day"
    since = (ctx.clock() - timedelta(days=NEEDS_ACTION_DAYS)).astimezone(timezone.utc).isoformat(timespec="seconds")
    needs = db.conn.execute(
        """SELECT message_id, subject, action_note, received_at FROM mail_items
           WHERE needs_action = 1 AND received_at >= ? ORDER BY received_at DESC""", (since,)).fetchall()
    month, day_local = local_period(ctx.clock())
    month_usd, today_usd = db.llm_spent(month, day_local)
    caps = ctx.cfg.llm.caps
    return Today(
        counts=Counts(new_today=count("first_seen_at >= :start AND first_seen_at < :end"),
                      deadlines_this_week=count(upcoming), starred=count("starred = 1"),
                      applied=count("status = 'applied'")),
        top_picks=rows(FOR_YOU, BEST_FIRST, TOP_PICKS),
        needs_action=[NeedsAction(**dict(n)) for n in needs],
        upcoming_deadlines=rows(upcoming, "deadline, score DESC, id"),
        spend=Spend(month_usd=month_usd, today_usd=today_usd, monthly_soft_cap_usd=caps.monthly_soft_usd,
                    daily_cap_usd=caps.daily_usd),
        status=Status(last_ok_check_at=db.get_state("last_ok_check_at"),
                      last_check_error=db.get_state("last_check_error"), problems=ctx.bus.problems()))


class SpendDay(BaseModel):
    day: str
    cost_usd: float
    calls: int
    input_tokens: int
    output_tokens: int


class SpendReport(BaseModel):
    days: list[SpendDay]
    month_usd: float
    monthly_soft_cap_usd: float


@router.get("/api/reports/spend")
def spend_report(_: Authorized, ctx: Ctx, db: Db, days: Annotated[int, Query(ge=1, le=365)] = 30) -> SpendReport:
    first = (today_in_hcmc(ctx) - timedelta(days=days - 1)).isoformat()
    found = db.conn.execute(
        """SELECT day_local AS day, SUM(cost_usd) AS cost_usd, COUNT(*) AS calls,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens
           FROM llm_calls WHERE day_local >= ? GROUP BY day_local ORDER BY day_local""", (first,)).fetchall()
    month, day_local = local_period(ctx.clock())
    return SpendReport(days=[SpendDay(**dict(r)) for r in found], month_usd=db.llm_spent(month, day_local)[0],
                       monthly_soft_cap_usd=ctx.cfg.llm.caps.monthly_soft_usd)


class Digest(BaseModel):
    digest_date: str
    created_at: str
    sent_at: str | None
    send_error: str | None
    item_count: int


class DigestReport(BaseModel):
    digests: list[Digest]


@router.get("/api/reports/digests")
def digests_report(_: Authorized, db: Db) -> DigestReport:
    found = db.conn.execute("SELECT digest_date, created_at, sent_at, send_error, item_ids FROM digests "
                            "ORDER BY digest_date DESC").fetchall()
    return DigestReport(digests=[Digest(digest_date=r["digest_date"], created_at=r["created_at"], sent_at=r["sent_at"],
                                        send_error=r["send_error"], item_count=len(json.loads(r["item_ids"])))
                                 for r in found])


class Week(BaseModel):
    week_start: str
    by_kind: dict[str, int]
    by_source: dict[str, int]


class ItemsReport(BaseModel):
    weeks: list[Week]


@router.get("/api/reports/items")
def items_report(_: Authorized, ctx: Ctx, db: Db, weeks: Annotated[int, Query(ge=1, le=52)] = 8) -> ItemsReport:
    day = today_in_hcmc(ctx)
    this_week = day - timedelta(days=day.weekday())  # Monday
    starts = [this_week - timedelta(weeks=n) for n in reversed(range(weeks))]
    kinds: dict[date, Counter] = {s: Counter() for s in starts}
    sources: dict[date, Counter] = {s: Counter() for s in starts}
    for r in db.conn.execute("SELECT first_seen_at, kind, source FROM opportunities WHERE first_seen_at >= ?",
                             (utc_text(starts[0]),)):
        seen = local_day(r["first_seen_at"])
        week = seen - timedelta(days=seen.weekday())
        if week in kinds:
            kinds[week][r["kind"]] += 1
            sources[week][r["source"]] += 1
    return ItemsReport(weeks=[Week(week_start=s.isoformat(), by_kind=dict(kinds[s]), by_source=dict(sources[s]))
                              for s in starts])


class GmailConnection(BaseModel):
    address: str
    signed_in: bool
    last_ok_check_at: str | None


class AnthropicConnection(BaseModel):
    key_saved: bool


class TelegramConnection(BaseModel):
    paired: bool


class Connections(BaseModel):
    gmail: GmailConnection
    anthropic: AnthropicConnection
    telegram: TelegramConnection


class Caps(BaseModel):
    monthly_soft_usd: float
    daily_usd: float


class ProfileSummary(BaseModel):
    level: str
    interests: list[str]
    open_to: list[str]
    home: str
    remote_ok: str


class Settings(BaseModel):
    connections: Connections
    caps: Caps
    for_you_min_score: int
    profile: ProfileSummary


def _key_saved() -> bool:
    from ..vault import load

    try:
        load("anthropic")
    except SecretError:
        return False
    return True


@router.get("/api/settings")
def settings(_: Authorized, ctx: Ctx, db: Db) -> Settings:
    from ..gmail_imap import token_fingerprint

    profile = ctx.holder.get()
    signed_in = token_fingerprint() is not None and db.get_state("last_check_error") != "auth_lost"
    return Settings(
        connections=Connections(
            gmail=GmailConnection(address=ctx.cfg.gmail_address, signed_in=signed_in,
                                  last_ok_check_at=db.get_state("last_ok_check_at")),
            anthropic=AnthropicConnection(key_saved=_key_saved()),
            telegram=TelegramConnection(paired=db.get_state("telegram_chat_id") is not None)),
        caps=Caps(monthly_soft_usd=ctx.cfg.llm.caps.monthly_soft_usd, daily_usd=ctx.cfg.llm.caps.daily_usd),
        for_you_min_score=ctx.cfg.dashboard.for_you_min_score,
        profile=ProfileSummary(level=profile.level, interests=list(profile.interests), open_to=list(profile.open_to),
                               home=profile.home, remote_ok=profile.remote_ok))


class Rescored(BaseModel):
    rescored: int
    expired: int


@router.post("/api/settings/rescore")
def rescore(_: Authorized, ctx: Ctx, db: Db) -> Rescored:
    profile = ctx.holder.get()
    rescored, expired = db.rescore_opportunities(lambda o: score(o, profile)[0], today_in_hcmc(ctx).isoformat())
    ctx.bus.publish(RANKING_CHANGED, {"rescored": rescored})
    return Rescored(rescored=rescored, expired=expired)
