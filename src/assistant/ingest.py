"""Gmail message ids -> stored mail. Shared by `assistant sync` and the engine's mail check.

Read-only like the rest: it only lists and fetches. Logs name a message by its
id and a reason, never its subject, sender or body.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .config import Config
from .gmail_imap import Mailbox, MessageGone, MessageRefused, UnreadableMail
from .parser import build_mail_item, is_outgoing
from .store import Store

# Mail that is never an opportunity for you: spam, the bin, chat logs, your own sent
# mail and drafts. (Gmail search excludes these too; the engine's feed of new UIDs doesn't.)
SKIP_LABELS = frozenset({"SPAM", "TRASH", "CHAT", "SENT", "DRAFT"})
PROGRESS_EVERY = 50

# Gmail search words for system labels and inbox categories, as label ids
_IN_LABELS = {"spam": "SPAM", "trash": "TRASH", "chats": "CHAT", "chat": "CHAT", "sent": "SENT", "drafts": "DRAFT",
              "draft": "DRAFT", "inbox": "INBOX", "important": "IMPORTANT", "starred": "STARRED"}
_CATEGORIES = {"primary": "CATEGORY_PERSONAL", "social": "CATEGORY_SOCIAL", "promotions": "CATEGORY_PROMOTIONS",
               "updates": "CATEGORY_UPDATES", "forums": "CATEGORY_FORUMS"}


def build_query(template: str, days: int) -> str:
    """Gmail search for a sync. Own sent mail and drafts are excluded here in
    code, because config.toml keeps its own copy of the query and an older
    copy would otherwise let them back in."""
    return f"{template.format(days=days)} -in:sent -in:drafts"


def query_exclusions(query: str) -> tuple[frozenset[str], list[str]]:
    """The labels a [gmail] query excludes (-in:spam, -category:promotions, ...), as
    label ids the feed of new mail can check, and the terms it can't check there
    (anything else: from:, -label:<your label>, OR...). newer_than: needs no check:
    the feed only ever holds new mail."""
    labels, unsupported = set(), []
    for term in query.split():
        word, _, value = term.lower().lstrip("-").partition(":")
        if term.startswith("newer_than:"):
            continue
        if term.startswith("-") and word in ("in", "label") and value in _IN_LABELS:
            labels.add(_IN_LABELS[value])
        elif term.startswith("-") and word == "category" and value in _CATEGORIES:
            labels.add(_CATEGORIES[value])
        else:
            unsupported.append(term)
    return frozenset(labels), unsupported


def ingest_ids(store: Store, mailbox: Mailbox, ids: Iterable[str], cfg: Config, my_addresses: tuple[str, ...],
               log: Callable[[str], None] = print, skip_labels: frozenset[str] = SKIP_LABELS,
               refused: list[str] | None = None) -> tuple[int, int]:
    """Fetch and store each id not stored yet. Returns (new, skipped): skipped counts
    ids already stored and messages not kept (gone, spam/chat/own, unreadable, refused).
    One broken or refused email never stops the rest. Refused ids go to `refused`,
    when given, so the caller can try them again."""
    new = skipped = 0
    try:
        for i, msg_id in enumerate(ids, start=1):
            if i % PROGRESS_EVERY == 0:
                log(f"  ...{i} checked")
            if store.has(msg_id):  # incremental: never re-download what we have
                skipped += 1
                continue
            try:
                raw = mailbox.get_message(msg_id)
            except MessageGone:
                log(f"  skipped message {msg_id}: gone before it could be read")
                skipped += 1
                continue
            except UnreadableMail as exc:
                log(f"  skipped message {msg_id}: could not read it ({exc.args[1]})")
                skipped += 1
                continue
            except MessageRefused:
                if refused is None:
                    log(f"  skipped message {msg_id}: Gmail wouldn't send it")
                else:
                    log(f"  message {msg_id}: Gmail wouldn't send it; trying again at the next check")
                    refused.append(msg_id)
                skipped += 1
                continue
            labels = skip_labels.intersection(raw.get("labelIds", ()))
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
            if store.add(item, my_addresses):
                new += 1
                store.commit()  # free the write lock before the next download: the API writes too
            else:
                skipped += 1
    finally:
        store.commit()  # keep what was parsed, even if the run stopped early
    return new, skipped


def search_sync(store: Store, mailbox: Mailbox, cfg: Config, days: int, my_addresses: tuple[str, ...],
                log: Callable[[str], None] = print, limit: int | None = None) -> tuple[int, int]:
    """Search the last `days` days and ingest what it finds, at most `limit` messages
    (default: [gmail] max_messages, newest first)."""
    refs = mailbox.search(build_query(cfg.gmail_query, days), limit or cfg.max_messages)
    return ingest_ids(store, mailbox, [r.id for r in refs], cfg, my_addresses, log=log)

