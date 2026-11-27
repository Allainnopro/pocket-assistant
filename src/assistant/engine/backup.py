"""Nightly backup: a gzipped, consistent copy of the database, one file per Ho Chi Minh City
date, the newest 7 kept. Only a file named assistant-YYYY-MM-DD.db.gz is ever created or removed."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

from ..config import Config
from ..costs import TIMEZONE
from .events import EventBus
from .jobs import Log

BACKUP_AT = time(3, 0)  # HCMC wall clock
KEEP = 7
NAME = re.compile(r"assistant-(\d{4}-\d{2}-\d{2})\.db\.gz")


def backup_folder(cfg: Config) -> Path:
    return cfg.db_path.parent / "backups"


def _name(day: date) -> str:
    return f"assistant-{day.isoformat()}.db.gz"


def make_backup(db_path: Path, folder: Path, day: date) -> Path:
    """A copy taken through SQLite's backup API, so a write in progress can't tear it. It is
    built under temporary names and moved into place last: a failure leaves no partial file."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / _name(day)
    tmp_db, tmp_gz = folder / f".{final.name}.db.tmp", folder / f".{final.name}.gz.tmp"
    try:
        source = sqlite3.connect(db_path)
        try:
            copy = sqlite3.connect(tmp_db)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        with tmp_db.open("rb") as raw, gzip.open(tmp_gz, "wb") as zipped:
            shutil.copyfileobj(raw, zipped)
        os.replace(tmp_gz, final)
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_gz.unlink(missing_ok=True)
    return final


def _dated(folder: Path) -> list[tuple[date, Path]]:
    found = []
    for path in folder.glob("assistant-*.db.gz") if folder.is_dir() else []:
        match = NAME.fullmatch(path.name)
        if match:
            try:
                found.append((date.fromisoformat(match.group(1)), path))
            except ValueError:  # 2026-13-45: not ours
                pass
    return sorted(found)


def prune(folder: Path, keep: int = KEEP) -> list[Path]:
    """Removes all but the newest `keep` backups; returns what it removed."""
    old = [path for _, path in _dated(folder)[:-keep]] if keep > 0 else [path for _, path in _dated(folder)]
    for path in old:
        path.unlink(missing_ok=True)
    return old


def latest(folder: Path) -> tuple[date, Path] | None:
    found = _dated(folder)
    return found[-1] if found else None


class BackupJob:
    """Runs hourly; after 03:00 HCMC it makes today's file if it isn't there yet, so a laptop
    that slept through the night still backs up once, and nothing backs up twice."""

    def __init__(self, db_path: Path, folder: Path, bus: EventBus, log: Log) -> None:
        self.db_path, self.folder, self.bus, self.log = db_path, folder, bus, log

    def __call__(self, now: datetime) -> None:
        local = now.astimezone(TIMEZONE)
        if local.time() < BACKUP_AT or (self.folder / _name(local.date())).exists():
            return
        try:
            make_backup(self.db_path, self.folder, local.date())
        except (OSError, sqlite3.Error) as exc:  # the type only: a message could carry a path or data
            self.log(f"backup: failed ({type(exc).__name__})")
            self.bus.report("backup", "backup_failed", detail=type(exc).__name__)
            return
        self.bus.report("backup", None)  # the file exists: a prune problem below is not a failed backup
        self.log(f"backup: saved {_name(local.date())}")
        try:
            removed = prune(self.folder)
        except OSError as exc:
            self.log(f"backup: could not remove old backups ({type(exc).__name__})")
        else:
            if removed:
                self.log(f"backup: removed {len(removed)} old")
