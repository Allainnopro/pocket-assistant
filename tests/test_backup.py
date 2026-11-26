"""Nightly backups.

Run: pytest
"""

import gzip
import sqlite3
from datetime import date, datetime, timezone

import pytest

from assistant.config import load_config
from assistant.engine import backup
from assistant.engine.backup import BackupJob, latest, make_backup, prune
from assistant.engine.events import EventBus
from assistant.errors import ConfigError


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "assistant.db"
    conn = sqlite3.connect(path)
    conn.execute("create table t (n integer)")
    conn.executemany("insert into t values (?)", [(i,) for i in range(5)])
    conn.commit()
    conn.close()
    return path


def test_a_backup_is_a_readable_copy(tmp_path, db):
    out = make_backup(db, tmp_path / "backups", date(2026, 11, 27))
    assert out.name == "assistant-2026-11-27.db.gz"
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(out.read_bytes()))
    assert sqlite3.connect(restored).execute("select count(*) from t").fetchone() == (5,)
    assert [p.name for p in (tmp_path / "backups").iterdir()] == [out.name]  # no temp files


def test_it_waits_for_three_in_the_morning_in_hcmc(tmp_path, db):
    folder = tmp_path / "backups"
    job = BackupJob(db, folder, EventBus(), print)
    job(datetime(2026, 11, 26, 19, 59, tzinfo=timezone.utc))  # 02:59 HCMC
    assert list(folder.glob("*.gz")) == []
    job(datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc))   # 03:00 HCMC
    job(datetime(2026, 11, 26, 21, 0, tzinfo=timezone.utc))
    assert [p.name for p in folder.glob("*.gz")] == ["assistant-2026-11-27.db.gz"]


def test_seven_are_kept_and_other_files_left_alone(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 10):
        (folder / f"assistant-2026-11-{day:02d}.db.gz").write_bytes(b"x")
    (folder / "notes.txt").write_text("keep me")
    removed = prune(folder)
    assert sorted(p.name for p in removed) == ["assistant-2026-11-01.db.gz", "assistant-2026-11-02.db.gz"]
    assert sorted(p.name for p in folder.iterdir()) == (
        [f"assistant-2026-11-{day:02d}.db.gz" for day in range(3, 10)] + ["notes.txt"])
    assert latest(folder)[0] == date(2026, 11, 9)
    assert latest(tmp_path / "nowhere") is None


def test_a_failed_backup_keeps_the_old_ones_and_says_so(tmp_path, db, monkeypatch):
    folder = tmp_path / "backups"
    old = make_backup(db, folder, date(2026, 11, 26))
    monkeypatch.setattr(backup.gzip, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full: C:/secret")))
    bus, logged = EventBus(), []
    BackupJob(db, folder, bus, logged.append)(datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc))
    assert [p.name for p in folder.iterdir()] == [old.name]
    assert bus.problems()["backup"] == {"problem": "backup_failed", "detail": "OSError"}
    assert logged == ["backup: failed (OSError)"]  # the type only, never the message


def test_a_prune_failure_is_not_a_failed_backup(tmp_path, db, monkeypatch):
    folder = tmp_path / "backups"
    monkeypatch.setattr(backup, "prune", lambda f: (_ for _ in ()).throw(PermissionError("locked")))
    bus, logged = EventBus(), []
    BackupJob(db, folder, bus, logged.append)(datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc))
    assert (folder / "assistant-2026-11-27.db.gz").exists()
    assert "backup" not in bus.problems()
    assert logged[-1] == "backup: could not remove old backups (PermissionError)"


@pytest.mark.parametrize("text", ['[backup]\nkeeper_device = "two"', "[backup]\nkeeper_device = 0",
                                  "[backup]\nkeeper_device = true", "[backup]\nfolder = 'x'"])
def test_backup_keeper_device_config(tmp_path, text):
    (tmp_path / "config.toml").write_text("[backup]\nkeeper_device = 2", encoding="utf-8")
    assert load_config(tmp_path / "config.toml").backup.keeper_device == 2
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[backup\]"):
        load_config(tmp_path / "config.toml")
