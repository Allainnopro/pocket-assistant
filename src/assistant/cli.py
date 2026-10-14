"""Command-line entry point: `assistant auth | sync | list | stats`.

Phase 0 has no UI on purpose. A CLI is the fastest way to prove the data
layer works before we spend weeks on the chibi. Later, the scheduler and
the FastAPI service call the same functions (sync_mail) directly.
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, load_config
from .parser import build_mail_item
from .store import Store


def sync_mail(cfg: Config, days: int) -> tuple[int, int]:
    """Pull recent mail into SQLite. Returns (new, skipped).

    Imported lazily so `list`/`stats` work even before Google libs are set up.
    """
    from .gmail_client import build_service, get_credentials, get_message, iter_message_ids

    service = build_service(get_credentials())
    store = Store(cfg.db_path)
    query = cfg.gmail_query.format(days=days)
    new = skipped = 0

    try:
        for i, msg_id in enumerate(iter_message_ids(service, query, cfg.max_messages), start=1):
            if store.has(msg_id):  # incremental: never re-download what we have
                skipped += 1
                continue
            raw = get_message(service, msg_id)
            item = build_mail_item(raw, cfg.university_addresses, cfg.max_body_chars)
            if store.add(item):
                new += 1
            if i % 50 == 0:  # commit in batches: a crash mid-sync loses little
                store.commit()
                print(f"  ...{i} checked", file=sys.stderr)
        store.commit()
    finally:
        store.close()
    return new, skipped


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="assistant", description="Personal assistant, Phase 0")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="log in to Gmail (opens a browser once)")

    p_sync = sub.add_parser("sync", help="pull recent Gmail messages into the database")
    p_sync.add_argument("--days", type=int, default=7)

    p_list = sub.add_parser("list", help="show recently stored emails")
    p_list.add_argument("--source", help="filter, e.g. linkedin, university, devpost")
    p_list.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="count stored emails by source")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "auth":
        from .gmail_client import login

        login(cfg.credentials_file)
        print("Logged in. Token saved to Windows Credential Manager.")

    elif args.command == "sync":
        new, skipped = sync_mail(cfg, args.days)
        print(f"Sync done: {new} new, {skipped} already stored.")

    elif args.command == "list":
        store = Store(cfg.db_path)
        for row in store.recent(args.limit, args.source):
            date = row["received_at"][:16].replace("T", " ")
            print(f"{date}  [{row['source']:<12}] {row['subject'][:70]}")
        store.close()

    elif args.command == "stats":
        store = Store(cfg.db_path)
        for row in store.counts_by_source():
            print(f"{row['source']:<14}{row['n']:>6}")
        store.close()


if __name__ == "__main__":
    main()
