"""New mail: the UIDs above a bookmark in All Mail, with a search fallback.

Why a bookmark: a search every 2 minutes would re-list a week of ids each time.
Asking for UIDs above the last one seen makes a quiet check one small command. IMAP
promises UIDs only keep rising for as long as UIDVALIDITY stays the same; if it
changes, the numbers mean nothing any more, and the check searches the days since
the last good check instead. Ids are stable either way, so nothing is fetched twice.

Read-only, like everything else: gmail_imap.Mailbox can only read.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..config import Config
from ..gmail_imap import Mailbox
from ..ingest import SKIP_LABELS, ingest_ids, query_exclusions, search_sync
from ..store import Store

MAX_SEARCH_DAYS = 7
# A message Gmail refuses holds the bookmark just below it, so it is tried again,
# but only this many checks in a row: one stuck message must not hold up all mail.
REFUSED_TRIES = 3
# The fallback moves the bookmark past everything it doesn't list, so it lists a
# whole week, not the CLI's max_messages. This only bounds a runaway listing.
FALLBACK_LIMIT = 5_000


@dataclass(frozen=True)
class CheckResult:
    new: int
    mode: Literal["feed", "search"]


def search_days(last_ok: str | None, now_utc: datetime) -> int:
    """Days to search back: since the last good check, rounded up, 1 to 7."""
    if last_ok is None:
        return MAX_SEARCH_DAYS
    elapsed = (now_utc - datetime.fromisoformat(last_ok)).total_seconds() / 86_400
    return max(1, min(MAX_SEARCH_DAYS, math.ceil(elapsed)))


def _hold_for(store: Store, held, last_uid: int, log: Callable[[str], None]) -> int:
    """The bookmark after a check where Gmail refused `held` (the lowest refused ref):
    just below it, so the next check tries it again, until REFUSED_TRIES in a row."""
    if held is None:
        store.set_state("imap_refused", None)
        return last_uid
    uid, _, count = (store.get_state("imap_refused") or "").partition(":")
    tries = int(count) + 1 if uid == str(held.uid) and count.isdigit() else 1
    if tries >= REFUSED_TRIES:
        log(f"  gave up on message {held.id}: Gmail refused it {tries} checks in a row")
        store.set_state("imap_refused", None)
        return last_uid
    store.set_state("imap_refused", f"{held.uid}:{tries}")
    return held.uid - 1


def check_mail(store: Store, mailbox: Mailbox, cfg: Config, *, now_utc: datetime,
               log: Callable[[str], None] = print) -> CheckResult:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware, e.g. datetime.now(timezone.utc)")
    my_addresses = (mailbox.address, *cfg.university_addresses)
    # Taken first: mail that lands while a search runs is then in the next check.
    # Fetching it twice is harmless (has() skips it).
    current = mailbox.bookmark()
    saved_uid = store.get_state("imap_last_uid")
    # A bookmark belongs to one account's All Mail: another account's numbers mean nothing
    if (saved_uid is not None and store.get_state("imap_uidvalidity") == current.uidvalidity
            and store.get_state("gmail_address") == mailbox.address):
        # The search applies [gmail] query itself; here its label exclusions are applied
        skip = SKIP_LABELS | query_exclusions(cfg.gmail_query)[0]
        refs = mailbox.list_new(int(saved_uid))
        ids = [r.id for r in refs if not skip.intersection(r.labels)]
        refused: list[str] = []
        new, _ = ingest_ids(store, mailbox, ids, cfg, my_addresses, log=log, skip_labels=skip, refused=refused)
        last_uid = max([int(saved_uid), *(r.uid for r in refs)])
        held = min((r for r in refs if r.id in refused), key=lambda r: r.uid, default=None)
        last_uid = _hold_for(store, held, last_uid, log)
        result = CheckResult(new=new, mode="feed")
    else:
        days = search_days(store.get_state("last_ok_check_at"), now_utc)
        new, skipped = search_sync(store, mailbox, cfg, days, my_addresses, log=log, limit=FALLBACK_LIMIT)
        if new + skipped >= FALLBACK_LIMIT:
            log(f"  warning: the search stopped at {FALLBACK_LIMIT} emails; older mail from those {days} days "
                "was not read")
        last_uid = current.last_uid
        store.set_state("imap_refused", None)
        result = CheckResult(new=new, mode="search")
    # The number first: a crash between the two writes then leaves an old UIDVALIDITY,
    # which only means one more search, never a new UIDVALIDITY with an old, high number
    store.set_state("imap_last_uid", str(last_uid))
    store.set_state("imap_uidvalidity", current.uidvalidity)
    store.set_state("gmail_address", mailbox.address)
    store.set_state("gmail_history_id", None)  # left over from the Gmail API
    store.set_state("last_ok_check_at", now_utc.isoformat())
    store.set_state("last_check_error", None)
    return result
