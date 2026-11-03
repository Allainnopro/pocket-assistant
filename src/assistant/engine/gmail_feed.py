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


def check_mail(store: Store, mailbox: Mailbox, cfg: Config, *, now_utc: datetime,
               log: Callable[[str], None] = print) -> CheckResult:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware, e.g. datetime.now(timezone.utc)")
    my_addresses = (mailbox.address, *cfg.university_addresses)
    # Taken first: mail that lands while a search runs is then in the next check.
    # Fetching it twice is harmless (has() skips it).
    current = mailbox.bookmark()
    saved_uid = store.get_state("imap_last_uid")
    if saved_uid is not None and store.get_state("imap_uidvalidity") == current.uidvalidity:
        # The search applies [gmail] query itself; here its label exclusions are applied
        skip = SKIP_LABELS | query_exclusions(cfg.gmail_query)[0]
        refs = mailbox.list_new(int(saved_uid))
        ids = [r.id for r in refs if not skip.intersection(r.labels)]
        new, _ = ingest_ids(store, mailbox, ids, cfg, my_addresses, log=log, skip_labels=skip)
        last_uid = max([int(saved_uid), *(r.uid for r in refs)])
        result = CheckResult(new=new, mode="feed")
    else:
        days = search_days(store.get_state("last_ok_check_at"), now_utc)
        new, skipped = search_sync(store, mailbox, cfg, days, my_addresses, log=log, limit=FALLBACK_LIMIT)
        if new + skipped >= FALLBACK_LIMIT:
            log(f"  warning: the search stopped at {FALLBACK_LIMIT} emails; older mail from those {days} days "
                "was not read")
        last_uid = current.last_uid
        result = CheckResult(new=new, mode="search")
    store.set_state("imap_uidvalidity", current.uidvalidity)
    store.set_state("imap_last_uid", str(last_uid))
    store.set_state("gmail_address", mailbox.address)
    store.set_state("gmail_history_id", None)  # left over from the Gmail API
    store.set_state("last_ok_check_at", now_utc.isoformat())
    store.set_state("last_check_error", None)
    return result
