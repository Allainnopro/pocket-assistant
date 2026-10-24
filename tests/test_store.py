"""Store and schema-migration tests, on throwaway databases in tmp_path.

Run: pytest
"""

import sqlite3

import pytest

from assistant.models import MailItem
from assistant.store import MIGRATIONS, Migration, Store, schema_version


def phase0_db(path, extra_rows=()):
    """A database exactly as Phase 0 left it on the owner's PC: version 0, row m1.

    extra_rows: (message_id, recipients, subject, labels_json) tuples."""
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
    for message_id, recipients, subject, labels in extra_rows:
        conn.execute(
            """INSERT INTO mail_items (message_id, thread_id, received_at, sender, sender_domain,
                   recipients, subject, snippet, body_text, source, labels)
               VALUES (?, 't', '2026-10-14T11:00:00+00:00', 'L <l@uts.edu.au>', 'uts.edu.au',
                       ?, ?, 'snip', 'body', 'university', ?)""",
            (message_id, recipients, subject, labels),
        )
    conn.commit()
    conn.close()


def mail(message_id="m1", **overrides):
    fields = dict(message_id=message_id, thread_id="t1", received_at="2026-10-14T10:00:00+00:00",
                  sender="A <a@x.com>", sender_domain="x.com", to_me=True, recipient_count=1,
                  subject="Hi", snippet="hi", body_text="body", source="other", labels=["INBOX"])
    fields.update(overrides)
    return MailItem(**fields)


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
    item = mail()
    assert store.add(item) is True
    assert store.add(item) is False  # duplicate message id is ignored
    store.commit()
    assert store.has("m1")
    assert [tuple(r) for r in store.counts_by_source()] == [("other", 1)]


def test_up_to_date_database_opens_while_another_process_writes(tmp_path):
    db = tmp_path / "a.db"
    step = [*MIGRATIONS, add_column("x")]
    writer = Store(db, migrations=step)
    writer.add(mail())
    assert writer.conn.in_transaction  # a sync mid-batch holds the write lock
    reader = Store(db, migrations=step)  # must not wait for that lock
    assert schema_version(reader.conn) == len(step)
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


CLASSMATE = "classmate.secret@student.example.edu.vn"


def test_v1_keeps_only_a_count_and_drops_own_sent_mail(tmp_path):
    db = tmp_path / "old.db"
    phase0_db(db, extra_rows=[
        ("class", f"Me <me@uts.edu.au>, Mate <{CLASSMATE}>, x@y.com", "Lab 4", '["INBOX"]'),
        ("mine", "lecturer@uts.edu.au", "My reply", '["SENT"]'),
        ("draft", "lecturer@uts.edu.au", "Unsent", '["DRAFT"]'),
    ])
    store = Store(db)
    assert schema_version(store.conn) == len(MIGRATIONS)
    assert "recipients" not in columns(store)
    row = store.conn.execute("SELECT to_me, recipient_count FROM mail_items WHERE message_id='class'").fetchone()
    assert (row["to_me"], row["recipient_count"]) == (None, 3)  # None = unknown for old rows
    assert store.has("m1") and not store.has("mine") and not store.has("draft")


def test_dropped_data_is_gone_from_the_file_itself(tmp_path):
    # Realistic sizes matter: a whole class list (~2 KB) spills into overflow
    # pages, and dropping the column leaves those bytes behind until VACUUM.
    # Tiny rows get rewritten in place and would pass even without the fix.
    db = tmp_path / "old.db"
    class_list = ", ".join(f"S{i} <s{i:02d}@student.uts.edu.au>" for i in range(45))
    phase0_db(db, extra_rows=[
        *[(f"class{i}", f"{class_list}, Mate <{CLASSMATE}>", "Lab", '["INBOX"]') for i in range(30)],
        ("mine", "lecturer@uts.edu.au", "OWN-SENT-MARKER" + "x" * 5000, '["SENT"]'),
    ])
    assert CLASSMATE.encode() in db.read_bytes()  # the test can see it before
    Store(db).close()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("old.db")]
    for path in leftovers:  # the .db plus any journal SQLite left behind
        data = path.read_bytes()
        assert CLASSMATE.encode() not in data, path.name
        assert b"OWN-SENT-MARKER" not in data, path.name


def test_new_rows_store_to_me_and_count_not_addresses(tmp_path):
    store = Store(tmp_path / "a.db")
    store.add(mail(to_me=False, recipient_count=45))
    store.commit()
    row = store.conn.execute("SELECT to_me, recipient_count FROM mail_items").fetchone()
    assert (row["to_me"], row["recipient_count"]) == (0, 45)


def test_every_connection_overwrites_deleted_content(tmp_path):
    store = Store(tmp_path / "a.db")
    assert store.conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


# --- Step 4: mail status, finish() and the retention sweep ----------------------

def test_status_columns_default_to_new_and_reject_unknown_values(tmp_path):
    store = Store(tmp_path / "a.db")
    store.add(mail())
    store.commit()
    assert store.conn.execute("SELECT status, attempts, injection_flag FROM mail_items").fetchone()[:] == ("new", 0, 0)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE mail_items SET status = 'oops'")


def test_finish_takes_mail_out_of_the_queue_and_deletes_its_body(tmp_path):
    store = Store(tmp_path / "a.db")
    store.add(mail("m1"))
    store.add(mail("m2"))
    store.commit()
    store.finish("m1", "filtered", "one-time-code")
    row = store.conn.execute(
        "SELECT processed, status, status_detail, body_text, body_purged_at FROM mail_items WHERE message_id='m1'"
    ).fetchone()
    assert row[:4] == (1, "filtered", "one-time-code", "") and row["body_purged_at"]
    assert [r["message_id"] for r in store.queue()] == ["m2"]
    with pytest.raises(ValueError):
        store.finish("m2", "new")


def test_finished_body_is_gone_from_the_file_bytes(tmp_path):
    db = tmp_path / "a.db"
    store = Store(db)
    store.add(mail("m1", body_text="BODY-MARKER " + "học phí " * 500))  # spills into overflow pages
    store.commit()
    store.finish("m1", "extracted")
    store.close()
    for path in tmp_path.iterdir():
        assert b"BODY-MARKER" not in path.read_bytes(), path.name


def test_sweep_expires_bodies_by_when_we_stored_them(tmp_path):
    store = Store(tmp_path / "a.db")
    store.add(mail("old_queued"))
    store.add(mail("old_done"))
    store.add(mail("late_sync", received_at="2026-09-01T10:00:00+00:00"))  # old mail, stored today
    store.commit()
    store.finish("old_done", "no_items")
    store.conn.execute("UPDATE mail_items SET body_text = 'x' WHERE message_id = 'old_done'")
    store.conn.execute(
        "UPDATE mail_items SET inserted_at = datetime('now', '-8 days') WHERE message_id IN ('old_queued', 'old_done')"
    )
    store.commit()
    assert store.purge_stale_bodies() == (2, 1)       # 2 bodies deleted, 1 never extracted
    rows = {r["message_id"]: (r["status"], r["body_text"]) for r in
            store.conn.execute("SELECT message_id, status, body_text FROM mail_items")}
    assert rows["old_queued"] == ("expired", "")      # never extracted: marked expired
    assert rows["old_done"] == ("no_items", "")       # keeps its real status
    assert rows["late_sync"] == ("new", "body")       # a vacation backlog is not expired on arrival
    assert store.purge_stale_bodies() == (0, 0)       # nothing left to sweep


def test_status_check_constraint_matches_the_status_list(tmp_path):
    from assistant.store import STATUSES
    store = Store(tmp_path / "a.db")
    store.add(mail())
    store.commit()
    for status in STATUSES:  # adding a status in code needs a new migration
        store.conn.execute("UPDATE mail_items SET status = ?", (status,))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE mail_items SET status = 'skipped'")


# --- M1: migrations v6-v7 and state ---------------------------------------------------

def test_v6_v7_upgrade_a_v5_database_and_keep_its_rows(tmp_path):
    db = tmp_path / "a.db"
    old = Store(db, migrations=MIGRATIONS[:5])
    old.conn.execute(
        """INSERT INTO opportunities (dedupe_key, base_key, kind, title, location_scope, relevance, score, why,
               source, first_message_id, first_seen_at)
           VALUES ('k|nodate', 'k', 'job', 'AI Engineer', 'hcmc', 80, 100, 'fits', 'linkedin', 'm1', '2026-10-01')"""
    )
    old.conn.commit()
    old.close()
    store = Store(db)
    assert schema_version(store.conn) == 7
    assert [tuple(r) for r in store.conn.execute("SELECT opportunity_id, message_id, seen_at FROM sightings")] == [
        (1, "m1", "2026-10-01")]
    assert store.conn.execute("SELECT starred FROM opportunities").fetchone()[0] == 0


def test_state_round_trips_and_none_deletes(tmp_path):
    store = Store(tmp_path / "a.db")
    assert store.get_state("gmail_history_id") is None
    store.set_state("gmail_history_id", "100")
    store.set_state("gmail_history_id", "120")
    assert store.get_state("gmail_history_id") == "120"
    store.set_state("gmail_history_id", None)
    assert store.get_state("gmail_history_id") is None
    assert Store(tmp_path / "a.db").get_state("gmail_history_id") is None  # committed, not just in memory


def test_item_actions_accept_only_known_actions(tmp_path):
    store = Store(tmp_path / "a.db")
    store.conn.execute("INSERT INTO item_actions (opportunity_id, action) VALUES (1, 'star')")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO item_actions (opportunity_id, action) VALUES (1, 'like')")
