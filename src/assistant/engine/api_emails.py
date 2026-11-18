"""The email routes and Ctrl+K search. Never the body, and never a sender's address."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..email_kinds import GREY, KINDS
from .api_deps import Authorized, Ctx, Db, fold_text, gmail_link, like, sender_name
from .api_opportunities import ROW_COLUMNS, OpportunityRow, row_model

router = APIRouter()

EmailKind = Literal[*KINDS]  # the dashboard's generated type is this union
URGENT_DAYS = 3
# The kind shown: the rules when sure, else the AI's answer, else the rules' (never grey) guess; mail stored
# before the kinds existed has neither until the engine re-sorts it, and shows as unsorted.
# Mail the free pre-filter dropped never reaches the AI: its verdict decides instead (codes and bank notices are
# account mail; the owner's own drop rules and Promotions mean noise).
FILTERED_KIND_SQL = ("CASE WHEN m.status_detail IN ('one-time-code', 'password-reset', 'security-alert', "
                     "'bank-notice') THEN 'accounts' ELSE 'promo' END")
KIND_SQL = ("COALESCE(CASE WHEN m.rule_sure = 1 THEN m.rule_kind "
            f"WHEN m.status = 'filtered' THEN {FILTERED_KIND_SQL} "
            "ELSE COALESCE(m.ai_kind, m.rule_kind) END, 'unsorted')")
# Urgent for three days after it arrived: :urgent_since is the clock minus URGENT_DAYS
URGENT_SQL = "((COALESCE(m.rule_urgent, 0) = 1 OR COALESCE(m.ai_urgent, 0) = 1) AND m.received_at >= :urgent_since)"
GREY_SQL = ", ".join(f"'{kind}'" for kind in sorted(GREY))
VISIBLE_SQL = f"({KIND_SQL} NOT IN ({GREY_SQL}) OR {URGENT_SQL})"  # Important, and Today's needs-action rows
MAIL_COLUMNS = ("m.message_id, m.received_at, m.sender, m.sender_domain, m.subject, m.snippet, m.source, "
                f"m.needs_action, m.action_note, m.injection_flag, {KIND_SQL} AS kind, {URGENT_SQL} AS urgent")
SIGHTINGS = "(SELECT COUNT(*) FROM sightings s WHERE s.message_id = m.message_id)"
IMPORTANT = f"({VISIBLE_SQL} OR m.injection_flag = 1)"


def urgent_since(clock) -> str:
    return (clock() - timedelta(days=URGENT_DAYS)).isoformat()
PAGES = {"today": "Today", "opportunities": "Opportunities", "emails": "Emails", "reports": "Reports",
         "settings": "Settings"}
MAX_EMAILS = 500  # the list is for reading, not for paging through: past this, `truncated` says so
PER_TYPE = 8


class EmailRow(BaseModel):
    message_id: str
    received_at: str
    sender_name: str
    subject: str
    excerpt: str
    source: str
    kind: EmailKind
    urgent: bool
    badges: list[Literal["opportunity", "needs_action", "addressed_ai"]]
    action_note: str | None
    opportunity_count: int


class EmailList(BaseModel):
    items: list[EmailRow]
    truncated: bool  # the window holds more than MAX_EMAILS: these are the newest


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
                                      ("needs_action", row["needs_action"]),
                                      ("addressed_ai", row["injection_flag"])) if on]
    return EmailRow(message_id=row["message_id"], received_at=row["received_at"],
                    sender_name=sender_name(row["sender"], row["sender_domain"]), subject=row["subject"],
                    excerpt=row["snippet"], source=row["source"], kind=row["kind"], urgent=bool(row["urgent"]),
                    badges=badges, action_note=row["action_note"], opportunity_count=row["opportunity_count"])


@router.get("/api/emails")
def list_emails(_: Authorized, ctx: Ctx, db: Db, section: Literal["important", "other"] = "important",
                days: Annotated[int, Query(ge=1, le=30)] = 7) -> EmailList:
    condition = IMPORTANT if section == "important" else f"NOT {IMPORTANT}"
    rows = db.conn.execute(
        f"""SELECT {MAIL_COLUMNS}, {SIGHTINGS} AS opportunity_count FROM mail_items m
            WHERE m.received_at >= :since AND {condition} ORDER BY m.received_at DESC LIMIT :limit""",
        {"since": (ctx.clock() - timedelta(days=days)).isoformat(), "urgent_since": urgent_since(ctx.clock),
         "limit": MAX_EMAILS + 1}).fetchall()
    return EmailList(items=[_row(r) for r in rows[:MAX_EMAILS]], truncated=len(rows) > MAX_EMAILS)


@router.get("/api/emails/{message_id}")
def email_detail(message_id: str, _: Authorized, ctx: Ctx, db: Db) -> EmailDetail:
    row = db.conn.execute(f"SELECT {MAIL_COLUMNS}, {SIGHTINGS} AS opportunity_count FROM mail_items m "
                          "WHERE m.message_id = :id", {"id": message_id, "urgent_since": urgent_since(ctx.clock)}).fetchone()
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
    wanted = fold_text(q)
    opportunities = db.conn.execute(
        r"""SELECT id, title, organizer FROM opportunities
            WHERE fold(title) LIKE ? ESCAPE '\' OR fold(organizer) LIKE ? ESCAPE '\'
            ORDER BY score DESC, id LIMIT ?""", (pattern, pattern, PER_TYPE)).fetchall()
    results = [SearchResult(type="opportunity", id=str(r["id"]), title=r["title"], subtitle=r["organizer"] or "")
               for r in opportunities]
    # The SQL also matches the address in `sender`, which doesn't count (names only), so it
    # can't stop at a fixed number of rows: read on until PER_TYPE real matches
    emails = db.conn.execute(
        r"""SELECT message_id, subject, sender, sender_domain FROM mail_items
            WHERE fold(subject) LIKE ? ESCAPE '\' OR fold(sender) LIKE ? ESCAPE '\'
            ORDER BY received_at DESC""", (pattern, pattern))
    for e in emails:
        name = sender_name(e["sender"], e["sender_domain"])
        if wanted in fold_text(e["subject"]) or wanted in fold_text(name):
            results.append(SearchResult(type="email", id=e["message_id"], title=e["subject"], subtitle=name))
            if len(results) - len(opportunities) == PER_TYPE:
                break
    emails.close()
    results += [SearchResult(type="page", id=page, title=title, subtitle="Page")
                for page, title in PAGES.items() if wanted in fold_text(title)]
    return SearchResults(results=results)
