"""The email routes and Ctrl+K search. Never the body, and never a sender's address."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from .api_deps import Authorized, Ctx, Db, gmail_link, like, sender_name
from .api_opportunities import ROW_COLUMNS, OpportunityRow, row_model

router = APIRouter()

MAIL_COLUMNS = ("m.message_id, m.received_at, m.sender, m.sender_domain, m.subject, m.snippet, m.source, "
                "m.needs_action, m.action_note, m.injection_flag")
SIGHTINGS = "(SELECT COUNT(*) FROM sightings s WHERE s.message_id = m.message_id)"
IMPORTANT = f"({SIGHTINGS} > 0 OR m.source = 'university' OR m.needs_action = 1 OR m.injection_flag = 1)"
PAGES = {"today": "Today", "opportunities": "Opportunities", "emails": "Emails", "reports": "Reports",
         "settings": "Settings"}
MAX_EMAILS = 500  # a week of mail: the list is for reading, not for paging through
PER_TYPE = 8


class EmailRow(BaseModel):
    message_id: str
    received_at: str
    sender_name: str
    subject: str
    excerpt: str
    source: str
    badges: list[Literal["opportunity", "university", "needs_action", "addressed_ai"]]
    action_note: str | None
    opportunity_count: int


class EmailList(BaseModel):
    items: list[EmailRow]


class EmailDetail(EmailRow):
    opportunities: list[OpportunityRow]
    gmail_url: str


class SearchResult(BaseModel):
    type: Literal["opportunity", "email", "page"]
    id: str
    title: str
    subtitle: str


class SearchResults(BaseModel):
    results: list[SearchResult]


def _row(row: sqlite3.Row) -> EmailRow:
    badges = [badge for badge, on in (("opportunity", row["opportunity_count"] > 0),
                                      ("university", row["source"] == "university"),
                                      ("needs_action", row["needs_action"]),
                                      ("addressed_ai", row["injection_flag"])) if on]
    return EmailRow(message_id=row["message_id"], received_at=row["received_at"],
                    sender_name=sender_name(row["sender"], row["sender_domain"]), subject=row["subject"],
                    excerpt=row["snippet"], source=row["source"], badges=badges, action_note=row["action_note"],
                    opportunity_count=row["opportunity_count"])


@router.get("/api/emails")
def list_emails(_: Authorized, ctx: Ctx, db: Db, section: Literal["important", "other"] = "important",
                days: Annotated[int, Query(ge=1, le=30)] = 7) -> EmailList:
    condition = IMPORTANT if section == "important" else f"NOT {IMPORTANT}"
    rows = db.conn.execute(
        f"""SELECT {MAIL_COLUMNS}, {SIGHTINGS} AS opportunity_count FROM mail_items m
            WHERE m.received_at >= ? AND {condition} ORDER BY m.received_at DESC LIMIT ?""",
        ((ctx.clock() - timedelta(days=days)).isoformat(), MAX_EMAILS)).fetchall()
    return EmailList(items=[_row(r) for r in rows])


@router.get("/api/emails/{message_id}")
def email_detail(message_id: str, _: Authorized, db: Db) -> EmailDetail:
    row = db.conn.execute(f"SELECT {MAIL_COLUMNS}, {SIGHTINGS} AS opportunity_count FROM mail_items m "
                          "WHERE m.message_id = ?", (message_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such email.")
    found = db.conn.execute(
        f"""SELECT {ROW_COLUMNS} FROM opportunities WHERE id IN
            (SELECT opportunity_id FROM sightings WHERE message_id = ?) ORDER BY score DESC, id""",
        (message_id,)).fetchall()
    return EmailDetail(**_row(row).model_dump(), opportunities=[row_model(r) for r in found],
                       gmail_url=gmail_link(db.get_state("gmail_address"), message_id))


@router.get("/api/search")
def search(q: Annotated[str, Query(min_length=2, max_length=100)], _: Authorized, db: Db) -> SearchResults:
    pattern = like(q)
    wanted = q.casefold()
    opportunities = db.conn.execute(
        r"""SELECT id, title, organizer FROM opportunities
            WHERE fold(title) LIKE ? ESCAPE '\' OR fold(organizer) LIKE ? ESCAPE '\'
            ORDER BY score DESC, id LIMIT ?""", (pattern, pattern, PER_TYPE)).fetchall()
    emails = db.conn.execute(
        r"""SELECT message_id, subject, sender, sender_domain FROM mail_items
            WHERE fold(subject) LIKE ? ESCAPE '\' OR fold(sender) LIKE ? ESCAPE '\'
            ORDER BY received_at DESC LIMIT ?""", (pattern, pattern, MAX_EMAILS)).fetchall()
    results = [SearchResult(type="opportunity", id=str(r["id"]), title=r["title"], subtitle=r["organizer"] or "")
               for r in opportunities]
    found_emails = [e for e in emails  # a match in the address part alone doesn't count: names only
                    if wanted in e["subject"].casefold()
                    or wanted in sender_name(e["sender"], e["sender_domain"]).casefold()]
    results += [SearchResult(type="email", id=e["message_id"], title=e["subject"],
                             subtitle=sender_name(e["sender"], e["sender_domain"])) for e in found_emails[:PER_TYPE]]
    results += [SearchResult(type="page", id=page, title=title, subtitle="Page")
                for page, title in PAGES.items() if wanted in title.casefold()]
    return SearchResults(results=results)
