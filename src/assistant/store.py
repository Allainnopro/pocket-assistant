"""SQLite storage. One file, zero setup, plenty fast for a single user.

Why SQLite and not Postgres/Supabase here? This is a personal, single-machine
tool: SQLite gives transactions and SQL queries with no server to run.
If it ever moves to a VPS, the same schema ports to Postgres easily.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import MailItem

# Version 0: the Phase 0 schema. Every later change is a numbered Migration
# below, so a brand-new database and an old one take the same upgrade path.
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


@dataclass(frozen=True)
class Migration:
    """One schema change. MIGRATIONS[i] upgrades version i to i + 1.

    `apply` must use conn.execute, never conn.executescript: executescript
    commits on its own and would break the all-or-nothing transaction.
    Set transactional=False only for statements SQLite refuses to run inside
    a transaction (VACUUM); the version is bumped only after they succeed.
    """

    name: str
    apply: Callable[[sqlite3.Connection], None]
    transactional: bool = True


MIGRATIONS: list[Migration] = []


def schema_version(conn: sqlite3.Connection) -> int:
    # PRAGMA user_version is a free integer in the database file header
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> int:
    """Bring the database up to the latest version. Safe to run on every open.

    Each migration runs in its own BEGIN IMMEDIATE transaction (it takes the
    write lock up front) and re-reads the version inside it, so two processes
    opening the database at once cannot both apply the same step. If a step
    fails, its transaction rolls back and the version stays where it was.

    An up-to-date database is detected with a plain read first, so read-only
    commands (`list`, `stats`) never wait for the write lock while a sync runs.
    """
    conn.executescript(SCHEMA)
    saved_isolation = conn.isolation_level
    conn.isolation_level = None  # we issue BEGIN/COMMIT ourselves
    try:
        for target, step in enumerate(migrations, start=1):
            if schema_version(conn) >= target:
                continue  # already applied: no lock needed
            if step.transactional:
                _locked(conn, lambda: _apply_if_behind(conn, step, target))
            else:
                # SQLite refuses VACUUM inside a transaction, so it runs bare.
                # Running it twice (two processes racing) is harmless; only the
                # version bump must be locked so a slow process can never move
                # the version backwards and make a later step run again.
                step.apply(conn)
                _locked(conn, lambda: _bump_if_behind(conn, target))
    finally:
        conn.isolation_level = saved_isolation
    return schema_version(conn)


def _apply_if_behind(conn: sqlite3.Connection, step: Migration, target: int) -> None:
    if schema_version(conn) < target:  # re-check under the lock
        step.apply(conn)
        conn.execute(f"PRAGMA user_version = {target}")


def _bump_if_behind(conn: sqlite3.Connection, target: int) -> None:
    if schema_version(conn) < target:
        conn.execute(f"PRAGMA user_version = {target}")


def _locked(conn: sqlite3.Connection, work: Callable[[], None]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        work()
        conn.execute("COMMIT")
    except BaseException:
        # SQLite rolls back by itself on some errors (e.g. disk full); a second
        # ROLLBACK would raise and hide the real error.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


class Store:
    def __init__(self, db_path: Path, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        # timeout: wait up to 10 s for another process's write lock instead of
        # failing at once. Default journal mode (no WAL): WAL keeps recently
        # deleted rows in a side file, which works against purging email bodies.
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row  # rows behave like dicts
        try:
            migrate(self.conn, migrations)
        except BaseException:
            self.conn.close()  # don't leave a half-open handle locking the file
            raise

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
