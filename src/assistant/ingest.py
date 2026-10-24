"""Gmail message ids -> stored mail. Shared by `assistant sync` and the engine's mail check.

Read-only like the rest: it only lists and fetches. Logs name a message by its
id and a reason, never its subject, sender or body.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .config import Config
from .parser import build_mail_item, is_outgoing
from .store import Store

# Mail that is never an opportunity for you: spam, the bin, chat logs, your own sent
# mail and drafts. (Gmail search excludes these too; the engine's history feed doesn't.)
SKIP_LABELS = frozenset({"SPAM", "TRASH", "CHAT", "SENT", "DRAFT"})
COMMIT_EVERY = 50


def build_query(template: str, days: int) -> str:
    """Gmail search for a sync. Own sent mail and drafts are excluded here in
    code, because config.toml keeps its own copy of the query and an older
    copy would otherwise let them back in."""
    return f"{template.format(days=days)} -in:sent -in:drafts"


def ingest_ids(store: Store, service, ids: Iterable[str], cfg: Config, my_addresses: tuple[str, ...],
               log: Callable[[str], None] = print) -> tuple[int, int]:
    """Fetch and store each id not stored yet. Returns (new, skipped): skipped counts
    ids already stored and messages not kept (gone, spam/chat/own, unparseable)."""
    # Imported here so `list`/`stats` work even before Google libs are set up
    from googleapiclient.errors import HttpError

    from .gmail_client import get_message

    new = skipped = 0
    try:
        for i, msg_id in enumerate(ids, start=1):
            if i % COMMIT_EVERY == 0:  # commit in batches: a crash mid-sync loses little
                store.commit()
                log(f"  ...{i} checked")
            if store.has(msg_id):  # incremental: never re-download what we have
                skipped += 1
                continue
            try:
                raw = get_message(service, msg_id)
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                log(f"  skipped message {msg_id}: gone before it could be read")
                skipped += 1
                continue
            labels = SKIP_LABELS.intersection(raw.get("labelIds", ()))
            if labels or is_outgoing(raw):
                log(f"  skipped message {msg_id}: {', '.join(sorted(labels)) or 'sent by you'}")
                skipped += 1
                continue
            try:
                item = build_mail_item(raw, cfg.university_addresses, cfg.max_body_chars, my_addresses)
            except Exception as exc:  # one odd email must never block the rest
                log(f"  skipped message {msg_id}: could not parse ({type(exc).__name__})")
                skipped += 1
                continue
            if store.add(item):
                new += 1
            else:
                skipped += 1
    finally:
        store.commit()  # keep what was parsed, even if the run stopped early
    return new, skipped


def search_sync(store: Store, service, cfg: Config, days: int, my_addresses: tuple[str, ...],
                log: Callable[[str], None] = print) -> tuple[int, int]:
    """Search the last `days` days and ingest what it finds."""
    from .gmail_client import iter_message_ids

    ids = iter_message_ids(service, build_query(cfg.gmail_query, days), cfg.max_messages)
    return ingest_ids(store, service, ids, cfg, my_addresses, log=log)
