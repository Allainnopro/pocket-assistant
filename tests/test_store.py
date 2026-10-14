"""Store and schema-migration tests, on throwaway databases in tmp_path.

Run: pytest
"""

import sqlite3

import pytest

from assistant.models import MailItem
from assistant.store import MIGRATIONS, Migration, Store, schema_version


def phase0_db(path):
    """A database exactly as Phase 0 left it on the owner's PC: version 0, one row."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE mail_items (
            message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, received_at TEXT NOT NULL,
            sender TEXT NOT NULL, sender_domain TEXT NOT NULL, recipients TEXT NOT NULL,
            subject TEXT NOT NULL, snippet TEXT NOT NULL, body_text TEXT NOT NULL,
            source TEXT NOT NULL, labels TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,
            inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO mail_items (message_id, thread_id, received_at, sender, sender_domain,
            recipients, subject, snippet, body_text, source, labels)
        VALUES ('m1', 't1', '2026-10-14T10:00:00+00:00', 'a@x.com', 'x.com', '',
                'Hello', 'hi', 'body', 'other', '[]');
        """
    )
    conn.close()


def add_column(name):
    return Migration(f"add {name}", lambda c: c.execute(f"ALTER TABLE mail_items ADD COLUMN {name} TEXT"))


def columns(store):
    return {r["name"] for r in store.conn.execute("PRAGMA table_info(mail_items)")}


def test_new_database_ends_at_latest_version(tmp_path):
    store = Store(tmp_path / "a.db", migrations=[add_column("x"), add_column("y")])
    assert schema_version(store.conn) == 2
    assert {"x", "y"} <= columns(store)


def test_phase0_database_upgrades_and_keeps_its_rows(tmp_path):
    db = tmp_path / "old.db"
    phase0_db(db)
    store = Store(db, migrations=[add_column("x")])
    assert schema_version(store.conn) == 1
    assert "x" in columns(store)
    assert store.has("m1")


def test_reopening_does_not_reapply_migrations(tmp_path):
    calls = []
    counted = Migration("count", lambda c: calls.append(1))
    Store(tmp_path / "a.db", migrations=[counted]).close()
    Store(tmp_path / "a.db", migrations=[counted]).close()
    assert calls == [1]


def test_failed_migration_rolls_back_completely(tmp_path):
    db = tmp_path / "old.db"
    phase0_db(db)

    def half_done_then_crash(conn):
        conn.execute("ALTER TABLE mail_items ADD COLUMN x TEXT")
        conn.execute("DELETE FROM mail_items")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        Store(db, migrations=[Migration("bad", half_done_then_crash)])

    store = Store(db, migrations=[])  # reopen without the broken step
    assert schema_version(store.conn) == 0
    assert "x" not in columns(store)
    assert store.has("m1")  # the delete was rolled back too


def test_non_transactional_step_runs_and_bumps_version(tmp_path):
    vacuum = Migration("vacuum", lambda c: c.execute("VACUUM"), transactional=False)
    store = Store(tmp_path / "a.db", migrations=[add_column("x"), vacuum])
    assert schema_version(store.conn) == 2


def test_default_store_roundtrip(tmp_path):
    store = Store(tmp_path / "a.db")
    assert schema_version(store.conn) == len(MIGRATIONS)
    item = MailItem("m1", "t1", "2026-10-14T10:00:00+00:00", "A <a@x.com>", "x.com",
                    "", "Hi", "hi", "body", "other", ["INBOX"])
    assert store.add(item) is True
    assert store.add(item) is False  # duplicate message id is ignored
    store.commit()
    assert store.has("m1")
    assert [tuple(r) for r in store.counts_by_source()] == [("other", 1)]


def test_up_to_date_database_opens_while_another_process_writes(tmp_path):
    db = tmp_path / "a.db"
    step = [add_column("x")]
    writer = Store(db, migrations=step)
    writer.add(MailItem("m1", "t1", "2026-10-14T10:00:00+00:00", "a@x.com", "x.com",
                        "", "Hi", "hi", "body", "other", []))
    assert writer.conn.in_transaction  # a sync mid-batch holds the write lock
    reader = Store(db, migrations=step)  # must not wait for that lock
    assert schema_version(reader.conn) == 1
    writer.conn.rollback()


def test_sqlite_auto_rollback_does_not_hide_the_real_error(tmp_path):
    db = tmp_path / "old.db"
    phase0_db(db)

    def duplicate_key(conn):
        # OR ROLLBACK makes SQLite end the transaction itself, like a full disk does
        conn.execute("INSERT OR ROLLBACK INTO mail_items SELECT * FROM mail_items")

    with pytest.raises(sqlite3.IntegrityError):
        Store(db, migrations=[Migration("dup", duplicate_key)])
    assert schema_version(Store(db, migrations=[]).conn) == 0


def test_racing_non_transactional_step_never_lowers_the_version(tmp_path):
    def vacuum_while_another_process_finishes(conn):
        # Simulate process A completing steps 2 and 3 while this VACUUM waited
        conn.execute("ALTER TABLE mail_items ADD COLUMN y TEXT")
        conn.execute("PRAGMA user_version = 3")

    steps = [add_column("x"),
             Migration("vacuum", vacuum_while_another_process_finishes, transactional=False),
             add_column("y")]
    store = Store(tmp_path / "a.db", migrations=steps)  # old code: duplicate column "y"
    assert schema_version(store.conn) == 3
