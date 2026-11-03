"""SQLite storage. One file, zero setup, plenty fast for a single user.

Why SQLite and not Postgres/Supabase here? This is a personal, single-machine
tool: SQLite gives transactions and SQL queries with no server to run.
If it ever moves to a VPS, the same schema ports to Postgres easily.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path

from .costs import (
    PRICES_AS_OF, Caps, Usage, allowed, attempts_cost, check_dollars, is_essential, local_period, total_usage,
)
from .errors import CapReached
from .models import MailItem
from .parser import parse_recipients
from .rank import NO_DEADLINE, Opportunity, keys, merge

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


def _v1_drop_recipient_lists(conn: sqlite3.Connection) -> None:
    """Privacy: Phase 0 stored whole To/Cc lists (entire classes). Keep a count only.

    to_me stays NULL ("unknown") for old rows: a migration has no access to
    config or Gmail, and it must not guess. Locally stored copies of the
    owner's own sent mail and drafts are deleted; Gmail itself is untouched.
    """
    conn.execute("ALTER TABLE mail_items ADD COLUMN to_me INTEGER")
    conn.execute("ALTER TABLE mail_items ADD COLUMN recipient_count INTEGER NOT NULL DEFAULT 0")
    for row in conn.execute("SELECT message_id, recipients FROM mail_items").fetchall():
        conn.execute(
            "UPDATE mail_items SET recipient_count = ? WHERE message_id = ?",
            (len(parse_recipients(row[1])[0]), row[0]),
        )
    conn.execute(
        """DELETE FROM mail_items WHERE EXISTS (
               SELECT 1 FROM json_each(mail_items.labels) WHERE value IN ('SENT', 'DRAFT'))"""
    )
    conn.execute("ALTER TABLE mail_items DROP COLUMN recipients")


def _v2_vacuum(conn: sqlite3.Connection) -> None:
    # DROP COLUMN leaves the old bytes in free pages; VACUUM rewrites the file without them
    conn.execute("VACUUM")


# Why an email left the extraction queue. `processed` keeps its Phase 0
# meaning (1 = left the queue, body gone); `status` says why.
STATUSES = ("new", "filtered", "extracted", "no_items", "refused", "invalid", "error", "expired")


def _v3_mail_status(conn: sqlite3.Connection) -> None:
    # Frozen text, deliberately not built from STATUSES: a migration must mean the
    # same thing forever. Adding a status later needs a new migration (a test
    # checks that STATUSES and this CHECK agree).
    conn.execute(
        "ALTER TABLE mail_items ADD COLUMN status TEXT NOT NULL DEFAULT 'new' CHECK (status IN "
        "('new', 'filtered', 'extracted', 'no_items', 'refused', 'invalid', 'error', 'expired'))"
    )
    conn.execute("ALTER TABLE mail_items ADD COLUMN status_detail TEXT")  # rule or error class, never mail text
    conn.execute("ALTER TABLE mail_items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE mail_items ADD COLUMN injection_flag INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE mail_items ADD COLUMN body_purged_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mail_queue ON mail_items(processed, inserted_at)")


def _v4_llm_calls(conn: sqlite3.Connection) -> None:
    """Every model call, one row: written BEFORE the call at its worst-case cost
    (outcome 'pending'), then corrected with the real usage. 'unknown' means
    the call may have been billed but no usage came back (timeout, crash): it
    keeps the estimate. Frozen text."""
    conn.execute(
        """CREATE TABLE llm_calls (
               id INTEGER PRIMARY KEY,
               ts_utc TEXT NOT NULL,
               month_local TEXT NOT NULL,          -- '2026-10', HCMC time
               day_local TEXT NOT NULL,            -- '2026-10-15'
               purpose TEXT NOT NULL,              -- extract | digest | smoke | record
               essential INTEGER NOT NULL,
               message_id TEXT,
               model_requested TEXT NOT NULL,
               model_served TEXT,
               request_id TEXT,
               stop_reason TEXT,
               refusal_category TEXT,
               input_tokens INTEGER,
               output_tokens INTEGER,               -- includes thinking
               thinking_tokens INTEGER,             -- informational subset of output
               cache_read_tokens INTEGER,
               cache_write_5m_tokens INTEGER,
               cache_write_1h_tokens INTEGER,
               attempts_json TEXT,                  -- per-attempt usage when a fallback ran
               cost_usd REAL NOT NULL,
               price_version TEXT NOT NULL,
               outcome TEXT NOT NULL CHECK (outcome IN
                   ('pending', 'ok', 'invalid', 'refused', 'max_tokens', 'api_error', 'unknown',
                    'skipped_cap')),
               error_class TEXT                     -- an exception class name, never a message
           )"""
    )
    conn.execute("CREATE INDEX idx_llm_month ON llm_calls(month_local)")
    conn.execute("CREATE INDEX idx_llm_day ON llm_calls(day_local)")


def _v5_opportunities(conn: sqlite3.Connection) -> None:
    """Deduped opportunities, scored against profile.toml. Frozen text: the kind and
    location lists are written out, and a test checks them against
    models.OPPORTUNITY_KINDS and extraction.LOCATION_SCOPES."""
    conn.execute(
        """CREATE TABLE opportunities (
               id INTEGER PRIMARY KEY,
               dedupe_key TEXT NOT NULL UNIQUE,      -- base_key + deadline
               base_key TEXT NOT NULL,               -- sorted title words + organizer (or link, or email)
               kind TEXT NOT NULL CHECK (kind IN ('hackathon', 'ideathon', 'competition', 'internship', 'job',
                   'job_fair', 'seminar', 'uni_notice', 'other')),
               title TEXT NOT NULL,
               organizer TEXT,
               deadline TEXT,                        -- YYYY-MM-DD
               location TEXT,
               location_scope TEXT NOT NULL CHECK (location_scope IN ('hcmc', 'vietnam_other', 'remote_vietnam',
                   'remote_international', 'abroad', 'unknown')),
               eligibility TEXT,
               prize_or_pay TEXT,
               url TEXT,
               relevance INTEGER NOT NULL CHECK (relevance BETWEEN 0 AND 100),
               score INTEGER NOT NULL,               -- uncapped: penalties must count at the top too
               why TEXT NOT NULL,
               flagged INTEGER NOT NULL DEFAULT 0,   -- an email it came from addressed the AI
               source TEXT NOT NULL,
               first_message_id TEXT NOT NULL,
               seen_count INTEGER NOT NULL DEFAULT 1,
               first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
               last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
               digested_on TEXT,
               status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'dismissed', 'applied'))
           )"""
    )
    conn.execute("CREATE INDEX idx_opp_base ON opportunities(base_key)")
    conn.execute("CREATE INDEX idx_opp_rank ON opportunities(status, score)")


def _v6_actions_and_sightings(conn: sqlite3.Connection) -> None:
    """Stars and the other item actions, and every email each opportunity was seen
    in (the dashboard links an item to all its emails). Frozen text."""
    conn.execute("ALTER TABLE opportunities ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """CREATE TABLE sightings (
               opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
               message_id TEXT NOT NULL,
               seen_at TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (opportunity_id, message_id))"""
    )
    conn.execute("CREATE INDEX idx_sightings_message ON sightings(message_id)")
    conn.execute(
        """INSERT INTO sightings (opportunity_id, message_id, seen_at)
           SELECT id, first_message_id, first_seen_at FROM opportunities"""
    )
    conn.execute(
        """CREATE TABLE item_actions (
               id INTEGER PRIMARY KEY,
               opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
               action TEXT NOT NULL CHECK (action IN ('star', 'unstar', 'dismiss', 'restore', 'applied')),
               at TEXT NOT NULL DEFAULT (datetime('now')))"""
    )


def _v7_needs_action_digests_state(conn: sqlite3.Connection) -> None:
    """Whether an email asks you to act, the daily digests, and the engine's small
    key/value state (Gmail bookmark, last good check, ...). Frozen text."""
    conn.execute("ALTER TABLE mail_items ADD COLUMN needs_action INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE mail_items ADD COLUMN action_note TEXT")
    conn.execute(
        """CREATE TABLE digests (
               digest_date TEXT PRIMARY KEY, text TEXT NOT NULL, html TEXT NOT NULL, item_ids TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT (datetime('now')), sent_at TEXT, send_error TEXT)"""
    )
    conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _v8_devices(conn: sqlite3.Connection) -> None:
    """Devices paired with the local API (the laptop). Only a hash of each key is kept."""
    conn.execute(
        """CREATE TABLE devices (
               id INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               key_hash TEXT NOT NULL UNIQUE,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               last_seen_at TEXT,
               revoked_at TEXT
           )"""
    )


MIGRATIONS: list[Migration] = [
    Migration("v1: drop recipient lists and own sent mail", _v1_drop_recipient_lists),
    Migration("v2: vacuum away the dropped data", _v2_vacuum, transactional=False),
    Migration("v3: mail status and body-purge tracking", _v3_mail_status),
    Migration("v4: LLM call ledger", _v4_llm_calls),
    Migration("v5: opportunities", _v5_opportunities),
    Migration("v6: actions and sightings", _v6_actions_and_sightings),
    Migration("v7: needs-action, digests and state", _v7_needs_action_digests_state),
    Migration("v8: devices", _v8_devices),
]

BODY_RETENTION_DAYS = 7


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


REPLY_OUTCOMES = ("ok", "invalid", "refused", "max_tokens")  # a response came back, with usage


class _NotQueued(Exception):
    """The email left the queue while its reply was on the way."""


def opportunity_from_row(row: sqlite3.Row) -> Opportunity:
    return Opportunity(**{f.name: row[f.name] for f in fields(Opportunity)})


@dataclass
class LlmCall:
    """A reserved model call; settle it once with what came back."""
    store: Store
    id: int
    settled: bool = False

    def settle(self, outcome: str, attempts: Sequence[tuple[str, Usage]] | None = None, **details) -> bool:
        changed = self.store.settle_llm_call(self.id, outcome=outcome, attempts=attempts, **details)
        self.settled = True
        return changed

    def abandon(self, error_class: str) -> None:
        if not self.settled:
            self.settle("unknown", error_class=error_class)


class Store:
    def __init__(self, db_path: Path, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        # timeout: wait up to 10 s for another process's write lock instead of
        # failing at once. Default journal mode (no WAL): WAL keeps recently
        # deleted rows in a side file, which works against purging email bodies.
        self.db_path = Path(db_path)  # worker threads open their own connection to it
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row  # rows behave like dicts
        # Overwrite deleted content with zeros instead of leaving it in free
        # pages, so purged email bodies are really gone from the file.
        self.conn.execute("PRAGMA secure_delete = ON")
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
               (message_id, thread_id, received_at, sender, sender_domain, to_me,
                recipient_count, subject, snippet, body_text, source, labels)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.message_id, item.thread_id, item.received_at, item.sender,
                item.sender_domain, item.to_me, item.recipient_count, item.subject,
                item.snippet, item.body_text, item.source, json.dumps(item.labels),
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

    def queue(self) -> list[sqlite3.Row]:
        """Emails still waiting for the brain, oldest first."""
        return self.conn.execute(
            """SELECT message_id, subject, sender, sender_domain, source, labels, received_at, body_text, attempts
               FROM mail_items WHERE processed = 0 ORDER BY received_at"""
        ).fetchall()

    def claim(self, message_id: str) -> sqlite3.Row | None:
        """Count an extraction attempt and re-read the email, IF it is still queued.

        None when it isn't any more: another run finished it, or the 7-day sweep
        deleted its body, since this run read the queue. The attempt is committed at
        once: if our own code crashes on an email run after run, the count shows it,
        and the email is given up on instead of blocking the queue for good.
        """
        with self.conn:
            if not self.conn.execute(
                "UPDATE mail_items SET attempts = attempts + 1 WHERE message_id = ? AND processed = 0", (message_id,)
            ).rowcount:
                return None
            return self.conn.execute(
                """SELECT message_id, subject, sender, sender_domain, source, labels, received_at, body_text,
                          attempts FROM mail_items WHERE message_id = ?""", (message_id,)
            ).fetchone()

    def unclaim(self, message_id: str) -> None:
        """Take an attempt back: the run stopped for a reason that isn't the email's."""
        with self.conn:
            self.conn.execute("UPDATE mail_items SET attempts = MAX(attempts - 1, 0) WHERE message_id = ?",
                              (message_id,))

    def finish(self, message_id: str, status: str, detail: str | None = None) -> bool:
        """Take an email out of the queue and delete its body, in one transaction.
        Returns False when it had already left the queue (nothing changed).

        CLAUDE.md: bodies are purged once extraction is done with them. With
        PRAGMA secure_delete on, the old text is overwritten, not just unlinked.
        """
        with self.conn:  # commits on success, rolls back on error
            return self._finish(message_id, status, detail)

    def _finish(self, message_id: str, status: str, detail: str | None) -> bool:
        """finish() without its own transaction, for callers that hold one. Only a
        queued email is finished: an email another run (or the sweep) already took
        out of the queue keeps its status. Returns whether it was still queued."""
        if status not in STATUSES or status == "new":
            raise ValueError(f"not a final status: {status}")
        return self.conn.execute(
            """UPDATE mail_items SET processed = 1, status = ?, status_detail = ?,
                   body_text = '', body_purged_at = datetime('now')
               WHERE message_id = ? AND processed = 0""",
            (status, detail, message_id),
        ).rowcount == 1

    def purge_stale_bodies(self, days: int = BODY_RETENTION_DAYS) -> tuple[int, int]:
        """Delete every body older than `days`, extracted or not.

        Returns (bodies deleted, of which still waiting for extraction): the
        second number is mail the brain will now never see, worth a warning.
        The privacy rule must hold even if extraction is broken for a week.
        Keyed on inserted_at (when WE stored it), compared inside SQLite, so
        mail synced late after a trip isn't expired before extraction gets
        a chance, and the two date formats never get compared as text.
        """
        cutoff = (f"-{int(days)} days",)
        with self.conn:
            expired = self.conn.execute(
                """SELECT COUNT(*) FROM mail_items
                   WHERE status = 'new' AND body_text <> '' AND inserted_at < datetime('now', ?)""",
                cutoff,
            ).fetchone()[0]
            cur = self.conn.execute(
                """UPDATE mail_items SET body_text = '', body_purged_at = datetime('now'), processed = 1,
                       status = CASE WHEN status = 'new' THEN 'expired' ELSE status END
                   WHERE body_text <> '' AND inserted_at < datetime('now', ?)""",
                cutoff,
            )
        return cur.rowcount, expired

    # --- LLM cost ledger -------------------------------------------------------

    def llm_spent(self, month_local: str, day_local: str) -> tuple[float, float]:
        """(this month, today) in USD, pending calls counted at their estimate."""
        month = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE month_local = ?", (month_local,)
        ).fetchone()[0]
        day = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day_local = ?", (day_local,)
        ).fetchone()[0]
        return float(month), float(day)

    @contextmanager
    def llm_call(
        self, *, now_utc: datetime, purpose: str, model: str, estimate_usd: float, caps: Caps,
        message_id: str | None = None,
    ) -> Iterator[LlmCall]:
        """The one way to make a model call: reserve, call, settle.

            with store.llm_call(...) as call:     # raises CapReached when a cap blocks it
                response = client.messages.create(...)
                call.settle("ok", attempts, request_id=...)

        If the block ends without settling (timeout, crash, Ctrl+C), the row
        becomes 'unknown' and keeps its worst-case estimate, because the server
        may have billed the call. Then the exception carries on as usual.
        """
        call = LlmCall(self, self.reserve_llm_call(
            now_utc=now_utc, purpose=purpose, model=model, estimate_usd=estimate_usd, caps=caps,
            message_id=message_id,
        ))
        try:
            yield call
        except BaseException as exc:
            try:
                call.abandon(type(exc).__name__)
            except Exception:
                pass  # e.g. database busy: the row stays 'pending' at its estimate, still safe
            raise
        else:
            call.abandon("NotSettled")  # a caller bug; the estimate stays

    def reserve_llm_call(
        self, *, now_utc: datetime, purpose: str, model: str, estimate_usd: float, caps: Caps,
        message_id: str | None = None,
    ) -> int:
        """Check the caps and record the call, in ONE locked transaction.

        Returns the id of a 'pending' row that holds the worst-case cost until
        settle_llm_call corrects it. When a cap blocks the call, a 'skipped_cap'
        row at $0 is committed and CapReached is raised. Locking first means
        two processes can't both slip under the cap.
        """
        check_dollars("estimate_usd", estimate_usd)
        essential = is_essential(purpose)
        month, day = local_period(now_utc)
        with self._immediate():
            decision = allowed(purpose, *self.llm_spent(month, day), estimate_usd, caps)
            cost, outcome = (estimate_usd, "pending") if decision.ok else (0.0, "skipped_cap")
            cur = self.conn.execute(
                """INSERT INTO llm_calls (ts_utc, month_local, day_local, purpose, essential, message_id,
                       model_requested, cost_usd, price_version, outcome, error_class)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now_utc.astimezone(timezone.utc).isoformat(), month, day, purpose, int(essential),
                 message_id, model, cost, PRICES_AS_OF, outcome, decision.reason),
            )
        if not decision.ok:
            raise CapReached(decision.reason)
        return cur.lastrowid

    def settle_llm_call(
        self, call_id: int, *, outcome: str, attempts: Sequence[tuple[str, Usage]] | None = None,
        request_id: str | None = None, stop_reason: str | None = None,
        refusal_category: str | None = None, error_class: str | None = None,
    ) -> bool:
        """Replace a pending row's estimate with what actually happened.

        `attempts` is [(model, Usage)], one per billed attempt in the response.
        The cost is priced HERE, each attempt at its own model, so no caller
        can price a fallback at the wrong model.
        - A reply (ok, invalid, refused, max_tokens) needs at least one attempt.
        - api_error (the server answered with an error, nothing billed): none, $0.
        - unknown (timeout, dropped connection, crash; the server may still have
          billed it): None, and the worst-case estimate stays.
        Returns False if the row was already settled: a second settle changes nothing.
        """
        attempts = None if attempts is None else list(attempts)  # check what will be stored
        if outcome == "unknown":
            if attempts is not None:
                raise ValueError("an unknown outcome keeps the estimate: pass no attempts")
        elif outcome == "api_error":
            if attempts:
                raise ValueError("an API error bills nothing: pass no attempts")
        elif outcome in REPLY_OUTCOMES:
            if not attempts:
                raise ValueError(f"outcome {outcome!r} needs the billed attempts from the response")
        else:
            raise ValueError(f"not a settled outcome: {outcome!r}")
        attempts = attempts or []
        for attempt in attempts:
            if not (isinstance(attempt, (tuple, list)) and len(attempt) == 2
                    and isinstance(attempt[0], str) and isinstance(attempt[1], Usage)):
                raise ValueError("attempts must be (model id, Usage) pairs")
            model, usage = attempt
            if usage.prompt_tokens < 1:  # every attempt reads the prompt; zero means a mapping bug
                raise ValueError(f"the attempt on {model} read no prompt tokens: its usage wasn't mapped")
        check_dollars("cost_usd", attempts_cost(attempts))  # catches a typo'd negative price
        with self.conn:
            if outcome == "unknown":
                cur = self.conn.execute(
                    """UPDATE llm_calls SET outcome = 'unknown', request_id = ?, error_class = ?
                       WHERE id = ? AND outcome = 'pending'""",
                    (request_id, error_class, call_id),
                )
            else:
                u = total_usage(attempts)
                cur = self.conn.execute(
                    """UPDATE llm_calls SET outcome = ?, cost_usd = ?, model_served = ?, request_id = ?,
                           stop_reason = ?, refusal_category = ?, input_tokens = ?, output_tokens = ?,
                           thinking_tokens = ?, cache_read_tokens = ?, cache_write_5m_tokens = ?,
                           cache_write_1h_tokens = ?, attempts_json = ?, error_class = ?
                       WHERE id = ? AND outcome = 'pending'""",
                    (outcome, attempts_cost(attempts), attempts[-1][0] if attempts else None, request_id,
                     stop_reason, refusal_category, u.input_tokens, u.output_tokens, u.thinking_tokens,
                     u.cache_read_tokens, u.cache_write_5m_tokens, u.cache_write_1h_tokens,
                     json.dumps([{"model": m, **asdict(a)} for m, a in attempts]) if len(attempts) > 1 else None,
                     error_class, call_id),
                )
            if cur.rowcount == 0 and not self.conn.execute(
                "SELECT 1 FROM llm_calls WHERE id = ?", (call_id,)
            ).fetchone():
                raise ValueError(f"no LLM call with id {call_id!r}")
        return cur.rowcount == 1

    def llm_summary(self, month_local: str) -> list[sqlite3.Row]:
        """Spend and tokens this month, by purpose, the model that answered, and outcome."""
        return self.conn.execute(
            """SELECT purpose, COALESCE(model_served, model_requested) AS model, outcome,
                      COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(thinking_tokens), 0) AS thinking_tokens,
                      COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                      COALESCE(SUM(cache_write_5m_tokens), 0) + COALESCE(SUM(cache_write_1h_tokens), 0)
                          AS cache_write_tokens
               FROM llm_calls WHERE month_local = ?
               GROUP BY purpose, COALESCE(model_served, model_requested), outcome ORDER BY cost DESC""",
            (month_local,),
        ).fetchall()

    @contextmanager
    def _immediate(self):
        """A write transaction that takes the lock up front (BEGIN IMMEDIATE)."""
        if self.conn.in_transaction:
            self.conn.commit()  # never fold someone's half-done batch into this one
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def release_orphaned_claims(self) -> int:
        """After a hard stop (the window closed, Windows restarted, a second Ctrl+C),
        calls that were on their way stay 'pending' and their emails keep a counted
        attempt: three such stops would give an email up unread. Call it only while
        holding engine.lock, when no run can be mid-call. The calls become 'unknown'
        (the estimate stays: they may have been billed) and the attempts go back.
        Returns how many emails got their attempt back."""
        with self._immediate():
            rows = self.conn.execute(
                "SELECT id, message_id FROM llm_calls WHERE outcome = 'pending' AND purpose = 'extract'"
            ).fetchall()
            self.conn.executemany(
                "UPDATE llm_calls SET outcome = 'unknown', error_class = 'Orphaned' WHERE id = ?",
                [(row["id"],) for row in rows])
            released = 0
            for message_id in {row["message_id"] for row in rows if row["message_id"]}:
                released += self.conn.execute(
                    "UPDATE mail_items SET attempts = MAX(attempts - 1, 0) WHERE message_id = ? AND processed = 0",
                    (message_id,)).rowcount
        return released

    def counts_by_status(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM mail_items GROUP BY status ORDER BY n DESC"
        ).fetchall()

    # --- opportunities ----------------------------------------------------------

    def save_opportunity(
        self, opp: Opportunity, *, message_id: str, source: str, flagged: bool,
        scorer: Callable[[Opportunity], int], today: str,
    ) -> tuple[int, bool]:
        """Insert, or merge into the same opportunity seen before. Returns (row id, merged)."""
        with self._immediate():  # look up and write under one lock: two writers can't both insert
            return self._save_opportunity(opp, message_id=message_id, source=source, flagged=flagged,
                                          scorer=scorer, today=today)

    def save_extraction(
        self, message_id: str, opps: Sequence[Opportunity], *, source: str, flagged: bool,
        scorer: Callable[[Opportunity], int], today: str, status: str, detail: str | None = None,
        needs_action: bool = False, action_note: str | None = None,
    ) -> list[tuple[int, bool]] | None:
        """Save one email's opportunities AND take the email out of the queue, in one
        transaction: a crash in between must not leave the items saved while the
        email waits to be extracted again (every item would then be counted twice).

        Returns None, saving nothing, if the email was no longer queued: a second
        run that extracted it at the same time must not count its items again.
        """
        try:
            with self._immediate():
                saved = [self._save_opportunity(o, message_id=message_id, source=source, flagged=flagged,
                                                scorer=scorer, today=today) for o in opps]
                if flagged:  # kept even when the email held no items: someone tried
                    self.conn.execute("UPDATE mail_items SET injection_flag = 1 WHERE message_id = ?",
                                      (message_id,))
                self.conn.execute("UPDATE mail_items SET needs_action = ?, action_note = ? WHERE message_id = ?",
                                  (int(needs_action), action_note, message_id))
                if not self._finish(message_id, status, detail):
                    raise _NotQueued  # roll every write above back
        except _NotQueued:
            return None
        return saved

    def _save_opportunity(
        self, opp: Opportunity, *, message_id: str, source: str, flagged: bool,
        scorer: Callable[[Opportunity], int], today: str,
    ) -> tuple[int, bool]:
        """The caller holds the transaction. Where a sighting goes, in order:
        1. the row with the same title, organizer and deadline;
        2. with a deadline: an open row of the same title and organizer still
           without one (it gets the deadline);
           without a deadline: the latest open row of the same title and
           organizer. Never an expired edition: a new, undated announcement
           would vanish into last year's hidden row;
        3. otherwise a new row.
        Exact key first, always: the other way round, an ordering of four emails
        made a deadline fill collide with an existing row (UNIQUE error).
        """
        base, dedupe = keys(opp, message_id)
        is_open = "status = 'active' AND (deadline IS NULL OR deadline >= ?)"
        row = self._one("SELECT * FROM opportunities WHERE dedupe_key = ?", dedupe)
        if row is None and opp.deadline:
            row = self._one(f"SELECT * FROM opportunities WHERE base_key = ? AND deadline IS NULL AND {is_open} "
                            "ORDER BY last_seen_at DESC, id DESC LIMIT 1", base, today)
        elif row is None:
            row = self._one(f"SELECT * FROM opportunities WHERE base_key = ? AND {is_open} "
                            "ORDER BY last_seen_at DESC, id DESC LIMIT 1", base, today)
        if row is None:
            cur = self.conn.execute(
                """INSERT INTO opportunities (dedupe_key, base_key, kind, title, organizer, deadline, location,
                       location_scope, eligibility, prize_or_pay, url, relevance, score, why, flagged, source,
                       first_message_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (dedupe, base, opp.kind, opp.title, opp.organizer, opp.deadline, opp.location,
                 opp.location_scope, opp.eligibility, opp.prize_or_pay, opp.url, opp.relevance,
                 scorer(opp), opp.why, int(flagged), source, message_id),
            )
            self._sighting(cur.lastrowid, message_id)
            return cur.lastrowid, False
        merged = merge(opportunity_from_row(row), opp)
        new_key = f"{row['base_key']}|{merged.deadline or NO_DEADLINE}"
        if new_key != row["dedupe_key"] and self._one("SELECT 1 FROM opportunities WHERE dedupe_key = ?", new_key):
            merged, new_key = replace(merged, deadline=row["deadline"]), row["dedupe_key"]  # never collide
        self.conn.execute(
            """UPDATE opportunities SET dedupe_key = ?, kind = ?, organizer = ?, deadline = ?, location = ?,
                   location_scope = ?, eligibility = ?, prize_or_pay = ?, url = ?, relevance = ?, why = ?,
                   score = ?, flagged = MAX(flagged, ?), seen_count = seen_count + 1,
                   last_seen_at = datetime('now')
               WHERE id = ?""",
            (new_key, merged.kind, merged.organizer, merged.deadline, merged.location, merged.location_scope,
             merged.eligibility, merged.prize_or_pay, merged.url, merged.relevance, merged.why,
             scorer(merged), int(flagged), row["id"]),
        )
        self._sighting(row["id"], message_id)
        return row["id"], True

    def _sighting(self, opportunity_id: int, message_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO sightings (opportunity_id, message_id) VALUES (?, ?)",
                          (opportunity_id, message_id))

    # --- engine state ---------------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        found = self._one("SELECT value FROM state WHERE key = ?", key)
        return found[0] if found else None

    def set_state(self, key: str, value: str | None) -> None:
        """Committed at once. None deletes the key."""
        with self.conn:
            if value is None:
                self.conn.execute("DELETE FROM state WHERE key = ?", (key,))
            else:
                self.conn.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def rescore_opportunities(self, scorer: Callable[[Opportunity], int], today: str) -> tuple[int, int]:
        """Score every row again (after a profile.toml edit) and expire what's past
        its deadline. Free: no model call. Returns (rescored, newly expired).
        Under the write lock, so a running extraction can't change a row between
        its read and its new score."""
        with self._immediate():
            rows = self.conn.execute("SELECT * FROM opportunities").fetchall()
            for row in rows:
                self.conn.execute("UPDATE opportunities SET score = ? WHERE id = ?",
                                  (scorer(opportunity_from_row(row)), row["id"]))
            expired = self.conn.execute(
                "UPDATE opportunities SET status = 'expired' WHERE status = 'active' AND deadline < ?", (today,)
            ).rowcount
        return len(rows), expired

    def active_opportunities(self, today: str) -> list[sqlite3.Row]:
        """Still open: active, and no deadline or a deadline today or later."""
        return self.conn.execute(
            """SELECT * FROM opportunities WHERE status = 'active' AND (deadline IS NULL OR deadline >= ?)
               ORDER BY score DESC, deadline IS NULL, deadline, last_seen_at DESC""",
            (today,),
        ).fetchall()

    def _one(self, sql: str, *params) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def counts_by_source(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM mail_items GROUP BY source ORDER BY n DESC"
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
