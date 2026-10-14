"""Command-line entry point: `assistant auth | sync | list | stats`.

Phase 0 has no UI on purpose. A CLI is the fastest way to prove the data
layer works before we spend weeks on the chibi. Later, the scheduler and
the FastAPI service call the same functions (sync_mail) directly.
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, load_config
from .errors import AssistantError
from .parser import build_mail_item, is_outgoing
from .store import Store


def build_query(template: str, days: int) -> str:
    """Gmail search for a sync. Own sent mail and drafts are excluded here in
    code, because config.toml keeps its own copy of the query and an older
    copy would otherwise let them back in."""
    return f"{template.format(days=days)} -in:sent -in:drafts"


def sync_mail(cfg: Config, days: int) -> tuple[int, int]:
    """Pull recent mail into SQLite. Returns (new, skipped).

    Imported lazily so `list`/`stats` work even before Google libs are set up.
    """
    from .gmail_client import (
        build_service, get_credentials, get_message, get_profile_address, iter_message_ids,
    )

    service = build_service(get_credentials())
    my_addresses = (get_profile_address(service), *cfg.university_addresses)
    store = Store(cfg.db_path)
    query = build_query(cfg.gmail_query, days)
    new = skipped = 0

    try:
        for i, msg_id in enumerate(iter_message_ids(service, query, cfg.max_messages), start=1):
            if store.has(msg_id):  # incremental: never re-download what we have
                skipped += 1
                continue
            raw = get_message(service, msg_id)
            if is_outgoing(raw):  # second guard; the query already excludes these
                continue
            try:
                item = build_mail_item(raw, cfg.university_addresses, cfg.max_body_chars, my_addresses)
            except Exception as exc:  # one odd email must never block the rest of the sync
                print(f"  skipped message {msg_id}: could not parse ({type(exc).__name__})", file=sys.stderr)
                continue
            if store.add(item):
                new += 1
            if i % 50 == 0:  # commit in batches: a crash mid-sync loses little
                store.commit()
                print(f"  ...{i} checked", file=sys.stderr)
    finally:
        try:
            store.commit()  # keep what was parsed, even if the sync stopped early
        finally:
            store.close()
    return new, skipped


def link_audit(cfg: Config, days: int) -> list[dict]:
    """Survey links in recent mail (read-only) and report what cleaning does to them."""
    from .gmail_client import build_service, get_credentials, get_message, iter_message_ids
    from .links import audit
    from .parser import raw_urls

    service = build_service(get_credentials())
    urls: list[str] = []
    for msg_id in iter_message_ids(service, build_query(cfg.gmail_query, days), cfg.max_messages):
        try:
            urls += raw_urls(get_message(service, msg_id).get("payload", {}))
        except Exception as exc:  # one odd email must not stop the survey
            print(f"  skipped message {msg_id} ({type(exc).__name__})", file=sys.stderr)
    return audit(urls)


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

    p_links = sub.add_parser(
        "links", help="survey links in recent mail: hosts, parameter names, what cleaning does (no values)"
    )
    p_links.add_argument("--days", type=int, default=30)

    args = parser.parse_args(argv)
    try:
        _run(args)
    except AssistantError as exc:
        # The one place an expected failure becomes an exit: message, no traceback
        raise SystemExit(str(exc)) from None


def _run(args: argparse.Namespace) -> None:
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

    elif args.command == "links":
        for row in link_audit(cfg, args.days):
            verdicts = ", ".join(f"{reason} {n}" for reason, n in row["verdicts"].most_common())
            print(f"{row['links']:>5}  {row['host']:<36} {verdicts}")
            for label, key in (("dropped params", "dropped_params"), ("kept params", "kept_params")):
                if row[key]:
                    print(f"       {label}: " + ", ".join(f"{k}({v})" for k, v in row[key].most_common()))


if __name__ == "__main__":
    main()
