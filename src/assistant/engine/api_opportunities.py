"""The opportunity routes: the tabs, one item's detail, and the actions on an item."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..rank import explain, score
from ..store import opportunity_from_row
from .api_deps import Authorized, Ctx, Db, gmail_link, like, sender_name, today_in_hcmc
from .events import ITEM_CHANGED

router = APIRouter()

OPEN = "status = 'active' AND (deadline IS NULL OR deadline >= :today)"
FOR_YOU = f"{OPEN} AND score >= :for_you"
TABS = {"for_you": FOR_YOU, "all": OPEN, "starred": "starred = 1", "applied": "status = 'applied'",
        "dismissed": "status = 'dismissed'"}
BEST_FIRST = "score DESC, deadline IS NULL, deadline, last_seen_at DESC, id"
ROW_COLUMNS = ("id, kind, title, organizer, deadline, location, location_scope, score, why, seen_count, starred, "
               "status, url, flagged, source, first_seen_at")


class OpportunityRow(BaseModel):
    id: int
    kind: str
    title: str
    organizer: str | None
    deadline: str | None
    location: str | None
    location_scope: str
    score: int
    why: str
    seen_count: int
    starred: bool
    status: str
    url: str | None
    flagged: bool
    source: str
    first_seen_at: str


class OpportunityList(BaseModel):
    tab_total: int
    total: int
    items: list[OpportunityRow]


class ScorePart(BaseModel):
    label: str
    points: int


class Sighting(BaseModel):
    message_id: str
    seen_at: str
    subject: str | None
    sender_name: str | None
    gmail_url: str


class OpportunityDetail(OpportunityRow):
    eligibility: str | None
    prize_or_pay: str | None
    breakdown: list[ScorePart]
    explanation: str
    sightings: list[Sighting]


def row_model(row: sqlite3.Row) -> OpportunityRow:
    return OpportunityRow(**{name: row[name] for name in OpportunityRow.model_fields})


@router.get("/api/opportunities")
def list_opportunities(
    _: Authorized, ctx: Ctx, db: Db,
    tab: Literal["for_you", "all", "starred", "applied", "dismissed"] = "for_you",
    kind: str | None = None,
    deadline_within: Annotated[int | None, Query(ge=0, le=365)] = None,
    min_score: int | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpportunityList:
    today = today_in_hcmc(ctx)
    params: dict = {"today": today.isoformat(), "for_you": ctx.cfg.dashboard.for_you_min_score}
    in_tab = TABS[tab]
    filters = []
    if kind:
        filters.append("kind = :kind")
        params["kind"] = kind
    if deadline_within is not None:
        filters.append("deadline BETWEEN :today AND :last_day")
        params["last_day"] = (today + timedelta(days=deadline_within)).isoformat()
    if min_score is not None:
        filters.append("score >= :min_score")
        params["min_score"] = min_score
    if q:
        filters.append(r"(title LIKE :q ESCAPE '\' OR organizer LIKE :q ESCAPE '\')")
        params["q"] = like(q)
    where = " AND ".join([f"({in_tab})", *filters])

    def count(condition: str) -> int:
        return db.conn.execute(f"SELECT COUNT(*) FROM opportunities WHERE {condition}", params).fetchone()[0]

    rows = db.conn.execute(
        f"SELECT {ROW_COLUMNS} FROM opportunities WHERE {where} ORDER BY {BEST_FIRST} LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return OpportunityList(tab_total=count(in_tab), total=count(where), items=[row_model(r) for r in rows])


def _found(db, opportunity_id: int) -> sqlite3.Row:
    row = db.conn.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such opportunity.")
    return row


@router.get("/api/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: int, _: Authorized, ctx: Ctx, db: Db) -> OpportunityDetail:
    row = _found(db, opportunity_id)
    __, parts = score(opportunity_from_row(row), ctx.holder.get())
    address = db.get_state("gmail_address")
    seen = db.conn.execute(
        """SELECT s.message_id, s.seen_at, m.subject, m.sender, m.sender_domain
           FROM sightings s LEFT JOIN mail_items m ON m.message_id = s.message_id
           WHERE s.opportunity_id = ? ORDER BY s.seen_at, s.message_id""", (opportunity_id,)).fetchall()
    return OpportunityDetail(
        **row_model(row).model_dump(), eligibility=row["eligibility"], prize_or_pay=row["prize_or_pay"],
        breakdown=[ScorePart(label=label, points=points) for label, points in parts], explanation=explain(parts),
        sightings=[Sighting(message_id=s["message_id"], seen_at=s["seen_at"], subject=s["subject"],
                            sender_name=sender_name(s["sender"], s["sender_domain"]) if s["sender"] else None,
                            gmail_url=gmail_link(address, s["message_id"])) for s in seen])


@router.post("/api/opportunities/{opportunity_id}/{action}")
def act_on_opportunity(opportunity_id: int, action: Literal["star", "unstar", "dismiss", "restore", "applied"],
                       _: Authorized, ctx: Ctx, db: Db) -> OpportunityRow:
    with db.transaction():
        row = _found(db, opportunity_id)
        if action in ("star", "unstar"):
            db.conn.execute("UPDATE opportunities SET starred = ? WHERE id = ?",
                            (int(action == "star"), opportunity_id))
        else:
            past = row["deadline"] is not None and row["deadline"] < today_in_hcmc(ctx).isoformat()
            new_status = {"dismiss": "dismissed", "applied": "applied"}.get(action, "expired" if past else "active")
            db.conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (new_status, opportunity_id))
        db.conn.execute("INSERT INTO item_actions (opportunity_id, action) VALUES (?, ?)", (opportunity_id, action))
    ctx.bus.publish(ITEM_CHANGED, {"id": opportunity_id, "action": action})
    return row_model(_found(db, opportunity_id))
