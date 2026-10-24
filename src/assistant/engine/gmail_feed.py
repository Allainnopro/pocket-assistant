"""New mail through Gmail's change feed (users.history.list), with a search fallback.

Why a feed: a search every 2 minutes would re-list a week of ids each time. The
feed lists only what arrived since a bookmark (a historyId), so a quiet check is
one small call. Gmail keeps history about a week; an older bookmark gets a 404,
and the check falls back to a search of the days since the last good check.

Read-only, like everything else: it lists and fetches, nothing more.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..config import Config
from ..ingest import SKIP_LABELS, ingest_ids, query_exclusions, search_sync
from ..store import Store

MAX_SEARCH_DAYS = 7
# The fallback moves the bookmark past everything it doesn't list, so it lists a
# whole week, not the CLI's max_messages. This only bounds a runaway listing.
FALLBACK_LIMIT = 5_000
# Moving mail OUT of these is how a message comes back: "Not spam", restore from the bin
RESCUE_LABELS = frozenset({"SPAM", "TRASH"})


class HistoryExpired(Exception):
    """The bookmark is older than the history Gmail keeps."""


@dataclass(frozen=True)
class CheckResult:
    new: int
    mode: Literal["history", "search"]


def fetch_new_ids(service, start_history_id: str,
                  skip_labels: frozenset[str] = SKIP_LABELS) -> tuple[list[str], str]:
    """Ids added since the bookmark, or rescued from spam or the bin, first-seen
    order, without duplicates or skipped labels; plus the bookmark to use next time."""
    from googleapiclient.errors import HttpError

    ids: dict[str, None] = {}  # an ordered set
    page_token = None
    while True:
        try:
            page = (service.users().history()
                    .list(userId="me", startHistoryId=start_history_id,
                          historyTypes=["messageAdded", "labelRemoved"], maxResults=500, pageToken=page_token)
                    .execute())
        except HttpError as exc:
            if exc.resp.status == 404:
                raise HistoryExpired from exc
            raise
        for record in page.get("history", []):
            for entry in record.get("messagesAdded", []):
                msg = entry["message"]
                if not skip_labels.intersection(msg.get("labelIds", ())):
                    ids.setdefault(msg["id"])
            for entry in record.get("labelsRemoved", []):  # labels it lost; its current ones are rechecked on fetch
                if RESCUE_LABELS.intersection(entry.get("labelIds", ())):
                    ids.setdefault(entry["message"]["id"])
        page_token = page.get("nextPageToken")
        if not page_token:
            return list(ids), str(page["historyId"])


def profile(service) -> tuple[str, str]:
    """(the signed-in address, lower-cased; the mailbox's current historyId)."""
    found = service.users().getProfile(userId="me").execute()
    return found["emailAddress"].lower(), str(found["historyId"])


def search_days(last_ok: str | None, now_utc: datetime) -> int:
    """Days to search back: since the last good check, rounded up, 1 to 7."""
    if last_ok is None:
        return MAX_SEARCH_DAYS
    elapsed = (now_utc - datetime.fromisoformat(last_ok)).total_seconds() / 86_400
    return max(1, min(MAX_SEARCH_DAYS, math.ceil(elapsed)))


def check_mail(store: Store, service, cfg: Config, *, now_utc: datetime,
               log: Callable[[str], None] = print) -> CheckResult:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware, e.g. datetime.now(timezone.utc)")
    bookmark, address = store.get_state("gmail_history_id"), store.get_state("gmail_address")
    result = None
    if bookmark and address:
        # The search applies [gmail] query itself; the feed applies its label exclusions
        skip = SKIP_LABELS | query_exclusions(cfg.gmail_query)[0]
        try:
            ids, next_bookmark = fetch_new_ids(service, bookmark, skip)
        except HistoryExpired:
            log("  the Gmail bookmark expired; searching instead")
        else:
            new, _ = ingest_ids(store, service, ids, cfg, (address, *cfg.university_addresses), log=log,
                                skip_labels=skip)
            result = CheckResult(new=new, mode="history")
    if result is None:
        # The bookmark comes first: mail that lands while the search runs is
        # then in the next feed. Fetching it twice is harmless (has() skips it).
        address, next_bookmark = profile(service)
        days = search_days(store.get_state("last_ok_check_at"), now_utc)
        new, skipped = search_sync(store, service, cfg, days, (address, *cfg.university_addresses), log=log,
                                   limit=FALLBACK_LIMIT)
        if new + skipped >= FALLBACK_LIMIT:
            log(f"  warning: the search stopped at {FALLBACK_LIMIT} emails; older mail from those {days} days "
                "was not read")
        result = CheckResult(new=new, mode="search")
    store.set_state("gmail_history_id", next_bookmark)
    store.set_state("gmail_address", address)
    store.set_state("last_ok_check_at", now_utc.isoformat())
    store.set_state("last_check_error", None)
    return result
