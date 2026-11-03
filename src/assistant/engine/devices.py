"""Devices paired with the local API (the laptop), and the one-time codes that pair them.

`assistant pair` shows a 6-digit code on the main computer. Typing it on the other
device trades it, once, for a long random key that the device keeps. Only hashes
are stored: a copy of the database can't be turned back into a working key.

A 6-digit code is short, so it lives for 5 minutes and dies after 5 wrong tries:
guessing it blind has at most a 5 in a million chance. Everything happens in one
write transaction, so two devices racing with the same code can't both win.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta

from ..errors import PairingError
from ..store import Store

CODE_TTL = timedelta(minutes=5)
MAX_TRIES = 5
LAST_SEEN_EVERY = timedelta(seconds=60)
HASH_KEY, EXPIRES_KEY, TRIES_KEY = "pairing_code_hash", "pairing_expires_at", "pairing_tries"
PAIR_AGAIN = "Run `assistant pair` again on your main computer."


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _put(store: Store, key: str, value: str) -> None:
    store.conn.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _clear_code(store: Store) -> None:
    store.conn.execute("DELETE FROM state WHERE key IN (?, ?, ?)", (HASH_KEY, EXPIRES_KEY, TRIES_KEY))


def new_pairing_code(store: Store, now_utc: datetime) -> str:
    """A new code replaces an unused one, and its tries start over."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    with store.transaction():
        _put(store, HASH_KEY, _hash(code))
        _put(store, EXPIRES_KEY, (now_utc + CODE_TTL).isoformat())
        _put(store, TRIES_KEY, "0")
    return code


def redeem_code(store: Store, code: str, device_name: str, now_utc: datetime) -> tuple[int, str]:
    """Trade a code for (device id, key). The key is shown this once and never stored.

    Raises PairingError, and its message never repeats the code. The error is raised
    after the transaction: a wrong try has to be counted, not rolled back.
    """
    refusal = None
    with store.transaction():
        state = dict(store.conn.execute("SELECT key, value FROM state WHERE key IN (?, ?, ?)",
                                        (HASH_KEY, EXPIRES_KEY, TRIES_KEY)).fetchall())
        if HASH_KEY not in state:
            refusal = f"No pairing code is waiting. {PAIR_AGAIN}"
        elif now_utc > datetime.fromisoformat(state[EXPIRES_KEY]):
            _clear_code(store)
            refusal = f"That pairing code has expired. {PAIR_AGAIN}"
        elif not hmac.compare_digest(_hash(code).encode(), state[HASH_KEY].encode()):
            tries = int(state[TRIES_KEY]) + 1
            if tries >= MAX_TRIES:
                _clear_code(store)
                refusal = f"Too many wrong tries, so that code is dead. {PAIR_AGAIN}"
            else:
                _put(store, TRIES_KEY, str(tries))
                refusal = "That pairing code is wrong."
        else:
            _clear_code(store)
            key = secrets.token_urlsafe(32)
            name = "".join(ch for ch in device_name if ch.isprintable()).strip()[:40] or "device"
            device_id = store.conn.execute(
                "INSERT INTO devices (name, key_hash, created_at) VALUES (?, ?, ?)",
                (name, _hash(key), now_utc.isoformat())).lastrowid
    if refusal:
        raise PairingError(refusal)
    return device_id, key


def device_for_key(store: Store, key: str, now_utc: datetime) -> int | None:
    """The device this key belongs to, or None when it is unknown or revoked."""
    row = store.conn.execute("SELECT id, last_seen_at FROM devices WHERE key_hash = ? AND revoked_at IS NULL",
                             (_hash(key),)).fetchone()
    if row is None:
        return None
    if row["last_seen_at"] is None or now_utc - datetime.fromisoformat(row["last_seen_at"]) >= LAST_SEEN_EVERY:
        try:
            with store.conn:
                store.conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?",
                                   (now_utc.isoformat(), row["id"]))
        except sqlite3.OperationalError:
            pass  # the database is busy: a stale "last seen" must not refuse a good key
    return row["id"]


def list_devices(store: Store) -> list[sqlite3.Row]:
    return store.conn.execute(
        "SELECT id, name, created_at, last_seen_at, revoked_at FROM devices ORDER BY id").fetchall()


def revoke(store: Store, device_id: int, now_utc: datetime) -> bool:
    """False when there is no such device, or it was already revoked."""
    with store.conn:
        return store.conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                  (now_utc.isoformat(), device_id)).rowcount > 0
