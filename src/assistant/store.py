"""SQLite storage. One file, zero setup, plenty fast for a single user.

Why SQLite and not Postgres/Supabase here? This is a personal, single-machine
tool: SQLite gives transactions and SQL queries with no server to run.
If it ever moves to a VPS, the same schema ports to Postgres easily.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import MailItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_items (
    message_id    TEXT PRIMARY KEY,      -- Gmail id: guarantees no duplicates
    thread_id     TEXT NOT NULL,
    received_at   TEXT NOT NULL,         -- ISO 8601 UTC, sorts correctly as text
    sender        TEXT NOT NULL,
    sender_domain TEXT NOT NULL,
    recipients    TEXT NOT NULL,
    subject       TEXT NOT NULL,
    snippet       TEXT NOT NULL,
    body_text     TEXT NOT NULL,         -- purged after Phase 1 extraction
    source        TEXT NOT NULL,
    labels        TEXT NOT NULL,         -- JSON array
    processed     INTEGER NOT NULL DEFAULT 0,  -- Phase 1 sets this after extraction
    inserted_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mail_received ON mail_items(received_at);
CREATE INDEX IF NOT EXISTS idx_mail_source   ON mail_items(source);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # rows behave like dicts
        self.conn.executescript(SCHEMA)

    def has(self, message_id: str) -> bool:
        """Checked BEFORE fetching a full message, so re-syncs cost almost nothing."""
        cur = self.conn.execute("SELECT 1 FROM mail_items WHERE message_id = ?", (message_id,))
        return cur.fetchone() is not None

    def add(self, item: MailItem) -> bool:
        """Insert; returns False if it already existed (INSERT OR IGNORE)."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO mail_items
               (message_id, thread_id, received_at, sender, sender_domain, recipients,
                subject, snippet, body_text, source, labels)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.message_id, item.thread_id, item.received_at, item.sender,
                item.sender_domain, item.recipients, item.subject, item.snippet,
                item.body_text, item.source, json.dumps(item.labels),
            ),
        )
        return cur.rowcount == 1

    def commit(self) -> None:
        self.conn.commit()

    def recent(self, limit: int = 20, source: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT received_at, source, sender, subject FROM mail_items"
        params: list = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def counts_by_source(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM mail_items GROUP BY source ORDER BY n DESC"
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
