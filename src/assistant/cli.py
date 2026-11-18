"""Command-line entry point: `assistant auth | sync | list | stats`.

Phase 0 has no UI on purpose. A CLI is the fastest way to prove the data
layer works before we spend weeks on the chibi. Later, the scheduler and
the FastAPI service call the same functions (sync_mail) directly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, load_config
from .email_kinds import configure as configure_kinds
from .errors import AssistantError
from .ingest import build_query, search_sync
from .store import BODY_RETENTION_DAYS, Store


def sync_mail(cfg: Config, days: int) -> tuple[int, int]:
    """Pull recent mail into SQLite. Returns (new, skipped)."""
    from . import gmail_imap

    with _reaching_gmail(), gmail_imap.open_mailbox(cfg) as mailbox:
        store = Store(cfg.db_path)
        try:
            return search_sync(store, mailbox, cfg, days, (mailbox.address, *cfg.university_addresses),
                               log=lambda line: print(line, file=sys.stderr))
        finally:
            try:
                sweep(store)  # the privacy rule holds even if `filter` is never run
            finally:
                store.close()


def sweep(store: Store) -> None:
    """Delete bodies past the retention window, and say so if any never got extracted."""
    deleted, unextracted = store.purge_stale_bodies()
    if unextracted:
        print(f"  {unextracted} email(s) waited more than {BODY_RETENTION_DAYS} days without being extracted: "
              "their bodies were deleted (privacy rule) and they will not be extracted.\n"
              "  To extract them anyway: delete assistant.db and run `assistant sync` again.", file=sys.stderr)
    elif deleted:
        print(f"  deleted {deleted} email bodies older than {BODY_RETENTION_DAYS} days", file=sys.stderr)


def run_filter(cfg: Config, dry_run: bool = False) -> dict[str, int]:
    """Apply the free pre-filter to queued emails. Returns {rule: count, "kept": n}.

    Dropped emails leave the queue with status "filtered" and their body is
    deleted. The retention sweep runs FIRST, on its own: a profile typo or a
    busy database must never stop old bodies from being deleted, and mail it
    expires must not be counted as "kept for extraction" (never in a dry run).
    """
    import json
    import sqlite3
    from collections import Counter

    from .errors import AssistantError
    from .filters import prefilter
    from .profile import load_profile

    store = Store(cfg.db_path)
    counts: Counter[str] = Counter()
    try:
        if not dry_run:
            sweep(store)
        profile = load_profile(cfg.profile_path)
        for row in store.queue():
            verdict = prefilter(row["subject"], row["sender_domain"], row["source"],
                                json.loads(row["labels"]), profile)
            if verdict.keep:
                counts["kept"] += 1
                continue
            counts[verdict.rule] += 1
            if dry_run:
                print(f"  would drop [{verdict.rule}] {row['source']}: {row['subject'][:70]}")
            else:
                store.finish(row["message_id"], "filtered", verdict.rule)
    except sqlite3.OperationalError as exc:
        raise AssistantError(f"The database is busy ({exc}); is a sync running? Try again in a minute.") from exc
    finally:
        store.close()
    dropped = sum(n for rule, n in counts.items() if rule != "kept")
    print(f"{'Would drop' if dry_run else 'Dropped'} {dropped}, kept {counts['kept']} for extraction.")
    for rule, n in counts.most_common():
        if rule != "kept":
            print(f"  {rule:<22}{n:>4}")
    return dict(counts)


def show_costs(cfg: Config, now_utc=None) -> None:
    """Month-to-date and today's LLM spend against the caps (HCMC calendar)."""
    from datetime import datetime, timezone

    from .costs import PRICES_AS_OF, local_period

    now_utc = now_utc or datetime.now(timezone.utc)
    month, day = local_period(now_utc)
    caps = cfg.llm.caps
    store = Store(cfg.db_path)
    try:
        month_spent, day_spent = store.llm_spent(month, day)
        rows = store.llm_summary(month)
    finally:
        store.close()
    pct = 100 * month_spent / caps.monthly_soft_usd
    print(f"LLM spend for {month} (Asia/Ho_Chi_Minh), prices as of {PRICES_AS_OF}")
    print(f"  this month: ${month_spent:.4f} of ${caps.monthly_soft_usd:.2f} soft cap ({pct:.0f}%)")
    print(f"  today:      ${day_spent:.4f} of ${caps.daily_usd:.2f} daily cap")
    if not rows:
        print("  no model calls yet this month")
        return
    print(f"  {'purpose':<8} {'model':<26} {'outcome':<12} {'calls':>5} {'cost':>9} {'input':>9} "
          f"{'output':>8} {'think/call':>10} {'cache rd':>9} {'cache wr':>9}")
    for r in rows:
        think = r["thinking_tokens"] / r["calls"]
        print(f"  {r['purpose']:<8} {r['model']:<26} {r['outcome']:<12} {r['calls']:>5} {'$' + format(r['cost'], '.4f'):>9} "
              f"{r['input_tokens']:>9,} {r['output_tokens']:>8,} {think:>10,.0f} "
              f"{r['cache_read_tokens']:>9,} {r['cache_write_tokens']:>9,}")
    if any(r["outcome"] in ("pending", "unknown") for r in rows):
        print("  pending/unknown calls count at their worst-case estimate (no usage came back).")


def _today_local(now_utc=None) -> str:
    from datetime import datetime, timezone

    from .costs import TIMEZONE

    return (now_utc or datetime.now(timezone.utc)).astimezone(TIMEZONE).date().isoformat()


def show_opportunities(cfg: Config, top: int, now_utc=None) -> list:
    """The open opportunities, best first, each with why it scored what it did.

    Scored live against profile.toml, so an edit shows up at once (run
    `assistant rescore` to store the new scores for the digest).
    """
    from .profile import load_profile
    from .rank import explain, score
    from .store import opportunity_from_row

    profile, today = load_profile(cfg.profile_path), _today_local(now_utc)
    store = Store(cfg.db_path)
    try:
        rows = store.active_opportunities(today)
    finally:
        store.close()
    ranked = sorted(((score(opportunity_from_row(r), profile), r) for r in rows),
                    key=lambda pair: (-pair[0][0], pair[1]["deadline"] or "9999"))[:top]
    if not ranked:
        print("No open opportunities yet. `assistant extract` (Step 8) fills this list.")
    for (value, parts), r in ranked:
        flag = "  [its email addressed the AI]" if r["flagged"] else ""
        print(f"{value:>3}  {r['kind']:<11} {r['title'][:60]}{flag}")
        place = f"{r['location']} [{r['location_scope']}]" if r["location"] else f"[{r['location_scope']}]"
        print(f"     {r['organizer'] or 'organizer unknown'} | {place} | {r['deadline'] or 'no deadline'}"
              f" | seen {r['seen_count']}x | {r['source']}")
        print(f"     {explain(parts)}")
        if r["url"]:
            print(f"     {r['url']}")
    return ranked


def rescore(cfg: Config, now_utc=None) -> tuple[int, int]:
    from .profile import load_profile
    from .rank import score

    profile = load_profile(cfg.profile_path)
    store = Store(cfg.db_path)
    try:
        rescored, expired = store.rescore_opportunities(lambda o: score(o, profile)[0], _today_local(now_utc))
    finally:
        store.close()
    print(f"Rescored {rescored} opportunities against profile.toml; {expired} newly past their deadline.")
    return rescored, expired


UNTRUSTED = ("Gmail's certificate didn't check out, so the password wasn't sent. This network may be "
             "intercepting connections; try another one.")


@contextmanager
def _reaching_gmail() -> Iterator[None]:
    """Network trouble ends in a message, not a traceback. Gmail's own words are left
    out: they don't help, and they could carry anything."""
    import imaplib
    import ssl

    from .gmail_imap import GmailBusy

    try:
        yield
    except PermissionError:
        raise  # the read-only gate tripping is a bug: let it show
    except ssl.SSLCertVerificationError:
        raise AssistantError(UNTRUSTED) from None
    except GmailBusy:
        raise AssistantError("Gmail is limiting this account for now. Nothing changed; try again in a few "
                             "minutes.") from None
    except (OSError, imaplib.IMAP4.error):
        raise AssistantError("Can't reach Gmail right now. Nothing changed; try again in a minute.") from None


def check_gmail(cfg: Config, password: str | None = None) -> None:
    """Sign in and open All Mail, read-only. Raises GmailAuthError when Gmail refuses,
    AssistantError when it can't be reached."""
    import keyring.errors

    from . import gmail_imap, vault

    with _reaching_gmail(), gmail_imap.open_mailbox(cfg, password=password) as mailbox:
        print(f"Signed in to Gmail as {mailbox.address}. All Mail is open, read-only.")
    try:
        removed = vault.remove_old_google_token()
    except keyring.errors.KeyringError:
        print("Couldn't remove the old Google sign-in from Credential Manager. It no longer works anyway; "
              "you can delete the 'gmail-oauth-token' entry there yourself.")
    else:
        if removed:
            print("Removed the old Google sign-in.")


def set_gmail_password(cfg: Config, ask=None, from_stdin: bool = False, stdin=None) -> None:
    """Test the App Password, then save it: one Gmail refuses never replaces one that works.
    One that can't be tested yet (no network, no address set) is saved, and you're told."""
    from . import vault
    from .errors import GmailAuthError
    from .gmail_imap import PASSWORD_REFUSED

    password = vault.check("gmail", _read_secret("gmail", ask, from_stdin, stdin))
    try:
        check_gmail(cfg, password=password)
    except GmailAuthError as exc:
        if str(exc) == PASSWORD_REFUSED:
            raise AssistantError("Gmail refused that App Password, so it wasn't saved; a password saved "
                                 "before is unchanged. Copy it again from https://myaccount.google.com/apppasswords"
                                 ) from None
        vault.save("gmail", password)
        print("Saved to Windows Credential Manager.")
        raise  # says what to do first: set [gmail] address, or sign in on the web
    except AssistantError as exc:
        vault.save("gmail", password)
        print(f"Saved to Windows Credential Manager, but not tested: {exc}")
        return
    vault.save("gmail", password)
    print("Saved to Windows Credential Manager.")


def set_key(kind: str, ask=None, from_stdin: bool = False, stdin=None) -> str:
    """Read a key without echoing it, and store it in Credential Manager.

    Never a command-line argument: arguments are saved in shell history and
    visible to other programs while this one runs. Two ways in:
    - a hidden prompt (getpass): nothing shows while you paste or type;
    - --stdin, for terminals where the hidden prompt gets no keystrokes:
      `Get-Clipboard | assistant set-key anthropic --stdin` pipes the copied
      key straight in, so it is never typed or shown.
    """
    from .vault import save

    hint = save(kind, _read_secret(kind, ask, from_stdin, stdin))
    print("Saved to Windows Credential Manager." + (f" The key ends in ...{hint}" if hint else ""))
    return hint


def _read_secret(kind: str, ask=None, from_stdin: bool = False, stdin=None) -> str:
    from getpass import getpass

    from .errors import SecretError
    from .vault import SECRETS

    label = SECRETS[kind].label
    if from_stdin:
        stdin = stdin or sys.stdin
        if stdin.isatty():  # typed here, the key would show on screen
            raise SecretError(f"--stdin expects the key piped in. Copy the {label}, then run:\n"
                              f"  Get-Clipboard | .\\.venv\\Scripts\\assistant.exe set-key {kind} --stdin")
        value = stdin.readline()
    else:
        ask = ask or getpass
        value = ask(f"Paste your {label} and press Enter. Nothing shows while you paste or type; "
                    "that's normal: ")
    return value


def llm_smoke(cfg: Config, llm=None, now_utc=None, shape: str = "tool", record: str | None = None,
              email: str = "devpost"):
    """One real extraction call on an invented email: checks the key, the exact
    request shape that ships, and the ledger. Costs well under $0.001."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from .costs import TIMEZONE, attempts_cost, total_usage
    from .extraction import SYNTHETIC_EMAILS, SYNTHETIC_PROFILE, extraction_request, parse_extraction
    from .llm import AnthropicLLM, call_logged
    from .profile import load_profile

    now_utc = now_utc or datetime.now(timezone.utc)
    # A recording is committed as a test fixture, and the reply's "why" lines echo
    # the profile: record with an invented candidate, never yours.
    profile = SYNTHETIC_PROFILE if record else load_profile(cfg.profile_path)
    request = extraction_request(SYNTHETIC_EMAILS[email], profile, now_utc.astimezone(TIMEZONE).date(),
                                 model=cfg.llm.extract_model, purpose="smoke", shape=shape)
    llm = llm or AnthropicLLM.from_vault()
    store = Store(cfg.db_path)
    try:
        result = call_logged(store, llm, request, parse_extraction, caps=cfg.llm.caps, now_utc=now_utc)
    finally:
        store.close()

    reply, used = result.reply, total_usage(result.reply.attempts)
    print(f"Smoke call to {reply.model} ({shape} shape): {result.outcome}, stop_reason {reply.stop_reason}")
    if result.outcome == "ok":
        print(f"  {len(result.value.items)} item(s), instructions_to_ai_detected={result.value.instructions_to_ai_detected}")
        for item in result.value.items:
            print(f"  - {item.kind:<12} {item.title[:50]:<50} {item.deadline or 'no deadline'} "
                  f"{item.location_scope} relevance {item.relevance}")
    elif result.outcome == "invalid":
        print(f"  the reply didn't fit the schema: {result.problem}")
        print(f"  it began: {reply.text[:200]!r}")
    elif result.outcome == "refused":
        print(f"  refusal category: {reply.refusal_category or 'none given'}")
    print(f"  tokens: input {used.input_tokens:,}, cache read {used.cache_read_tokens:,}, "
          f"cache write {used.cache_write_5m_tokens + used.cache_write_1h_tokens:,}, "
          f"output {used.output_tokens:,} (thinking {used.thinking_tokens:,})")
    print(f"  cost ${attempts_cost(reply.attempts):.6f}, request id {reply.request_id}")
    if record:
        Path(record).write_text(json.dumps(reply.raw, indent=1, ensure_ascii=False, default=str) + "\n",
                                encoding="utf-8", newline="\n")
        print(f"  reply saved to {record} (invented email and candidate: nothing private in it)")
    return result


def extract(cfg: Config, llm=None, clock=None, max_calls: int | None = None, estimate: bool = False):
    """Run the queue through Haiku 5.5 (see pipeline.py), or with estimate=True,
    only count what would be sent and what it could cost. Nothing is sent then."""
    from .costs import local_period
    from .llm import AnthropicLLM
    from .pipeline import MAX_CALLS_PER_RUN, run_extraction, utc_now
    from .profile import load_profile

    profile, clock = load_profile(cfg.profile_path), clock or utc_now
    max_calls = max_calls or MAX_CALLS_PER_RUN
    store = Store(cfg.db_path)
    try:
        sweep(store)  # the privacy rule first: bodies past their 7 days are gone either way
        if estimate:
            return _estimate(store, profile, cfg, clock, max_calls)
        store.release_orphaned_claims()  # main() holds engine.lock: no other run is mid-call
        month = local_period(clock())[0]
        spent_before = store.llm_spent(month, "")[0]
        report = run_extraction(store, llm or AnthropicLLM.from_vault(), profile, model=cfg.llm.extract_model,
                                caps=cfg.llm.caps, clock=clock, max_calls=max_calls)
        cost = store.llm_spent(month, "")[0] - spent_before
        left = len(store.queue())
    finally:
        store.close()
    c = report.counts
    print(f"Done: {c['extracted']} emails with opportunities, {c['no_items']} without, {c['filtered']} dropped "
          f"free by the pre-filter, {c['refused']} refused, {c['invalid']} invalid, {c['error']} given up.")
    print(f"Opportunities: {c['new']} new, {c['merged']} seen again. {report.calls} model calls, ${cost:.4f}.")
    if report.fatal:
        print(f"Stopped: {report.stopped}. {left} email(s) still queued, untouched.")
        raise report.fatal  # the reason, and how to fix it, after the summary
    if report.stopped:
        print(f"Stopped early: {report.stopped}. {left} email(s) still queued; run it again later.")
    elif left:
        print(f"{left} email(s) still queued.")
    print("See them with: assistant opportunities --top 20")
    return report


def _estimate(store, profile, cfg: Config, clock, max_calls: int):
    import json

    from .costs import TIMEZONE, estimate_usd
    from .extraction import email_for_model, extraction_request
    from .filters import prefilter

    from .pipeline import MAX_ATTEMPTS

    today = clock().astimezone(TIMEZONE).date()
    rows = store.queue()
    sent = [r for r in rows if r["attempts"] < MAX_ATTEMPTS and prefilter(
        r["subject"], r["sender_domain"], r["source"], json.loads(r["labels"]), profile).keep]
    worst = 0.0
    for r in sent:  # the first call, plus the retry with twice the room a cut-off reply gets
        request = extraction_request(email_for_model(r), profile, today, model=cfg.llm.extract_model)
        bound = request.prompt_bound()
        worst += estimate_usd(request.model, bound, request.max_tokens)
        worst += estimate_usd(request.model, bound, 2 * request.max_tokens)
    print(f"{len(sent)} of {len(rows)} queued emails would go to {cfg.llm.extract_model}; "
          f"{len(rows) - len(sent)} would be dropped free by the pre-filter. Nothing was sent.")
    print(f"Expected cost: about ${0.0004 * len(sent):.4f} ($0.0003-0.0006 an email, measured). "
          f"Worst case, every reply at its longest: ${worst:.4f}.")
    if len(sent) > max_calls:
        print(f"One run makes at most {max_calls} calls: run it {-(-len(sent) // max_calls)} times, "
              f"or pass --max-calls {len(sent)}.")
    return len(sent), worst


def link_audit(cfg: Config, days: int) -> list[dict]:
    """Survey links in recent mail (read-only) and report what cleaning does to them."""
    from . import gmail_imap
    from .links import audit
    from .parser import raw_urls

    urls: list[str] = []
    with _reaching_gmail(), gmail_imap.open_mailbox(cfg) as mailbox:
        for ref in mailbox.search(build_query(cfg.gmail_query, days), cfg.max_messages):
            try:
                urls += raw_urls(mailbox.get_message(ref.id).get("payload", {}))
            except Exception as exc:  # one odd email must not stop the survey
                print(f"  skipped message {ref.id} ({type(exc).__name__})", file=sys.stderr)
    return audit(urls)


def main(argv: list[str] | None = None) -> None:
    # Windows consoles and pipes default to cp1252, which can't print Vietnamese
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="assistant", description="Personal assistant")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="test the sign-in to Gmail with the saved App Password")

    p_sync = sub.add_parser("sync", help="pull recent Gmail messages into the database")
    p_sync.add_argument("--days", type=int, default=7)

    p_list = sub.add_parser("list", help="show recently stored emails")
    p_list.add_argument("--source", help="filter, e.g. linkedin, university, devpost")
    p_list.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="count stored emails by source and by status")

    sub.add_parser("costs", help="LLM spend this month and today, against the caps")

    p_extract = sub.add_parser("extract", help="read queued emails with Haiku 5.5 into ranked opportunities")
    p_extract.add_argument("--max-calls", type=int, choices=range(1, 201), metavar="N",
                           help="model calls allowed this run (default 60, at most 200)")
    p_extract.add_argument("--estimate", action="store_true",
                           help="count what would be sent and what it could cost; send nothing")

    p_opp = sub.add_parser("opportunities", help="open opportunities, best first, with why each scored")
    p_opp.add_argument("--top", type=int, default=10)

    sub.add_parser("rescore", help="re-rank every opportunity after editing profile.toml (free)")

    p_key = sub.add_parser("set-key", help="save an API key or the Gmail App Password in Windows Credential Manager (hidden prompt)")
    p_key.add_argument("kind", choices=["anthropic", "gmail"])
    p_key.add_argument("--stdin", action="store_true",
                       help="read the key from a pipe: Get-Clipboard | assistant set-key anthropic --stdin")

    p_llm = sub.add_parser("llm", help="model-call tools")
    llm_sub = p_llm.add_subparsers(dest="llm_command", required=True)
    p_smoke = llm_sub.add_parser("smoke", help="one real call on an invented email (costs < $0.001)")
    p_smoke.add_argument("--shape", choices=["tool", "plain"], default="tool",
                         help="how the email is framed: as a tool result (default) or in <email> tags")
    p_smoke.add_argument("--record", metavar="PATH", help="save the reply as a test fixture")
    p_smoke.add_argument("--email", default="devpost", choices=["devpost", "linkedin", "uni-notice", "injection",
                                                                "newsletter"],
                         help="which invented email to send (default devpost)")

    p_filter = sub.add_parser("filter", help="drop obvious noise for free, before any model call")
    p_filter.add_argument("--dry-run", action="store_true", help="show what would be dropped, change nothing")

    sub.add_parser("api-schema", help="write app/src/api/openapi.json, the API contract the dashboard's types come from")

    p_engine = sub.add_parser("engine", help="keep running: check mail every 2 minutes and extract it as it arrives")
    p_engine.add_argument("--dev", action="store_true",
                          help="for building the dashboard: print the launch password, allow Vite's dev server")

    sub.add_parser("pair", help="show a one-time code that connects another device (your laptop) to this PC")
    p_devices = sub.add_parser("devices", help="list the paired devices")
    devices_sub = p_devices.add_subparsers(dest="devices_command")
    p_revoke = devices_sub.add_parser("revoke", help="cut a paired device off, at once")
    p_revoke.add_argument("id", type=int)

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
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc) and "busy" not in str(exc):
            raise
        raise SystemExit("The database is busy (is a sync running?). Try again in a minute.") from None


API_SCHEMA = Path(__file__).resolve().parents[2] / "app" / "src" / "api" / "openapi.json"  # this repo's


def write_api_schema() -> None:
    from .engine.api import openapi_document

    API_SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    # LF on every OS, so the committed file and `npm run check:api` agree
    API_SCHEMA.write_text(json.dumps(openapi_document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                          newline="\n")
    print(f"Wrote {API_SCHEMA}. Then, in app/: npm run gen:api")


def pair(cfg: Config) -> None:
    from .engine.devices import CODE_TTL, new_pairing_code

    store = Store(cfg.db_path)
    try:
        code = new_pairing_code(store, datetime.now(timezone.utc))
    finally:
        store.close()
    print(f"Pairing code: {code} (works once, for {CODE_TTL.seconds // 60} minutes)")
    host = cfg.api.tailnet_host
    if not host:
        print("Set [api] tailnet_host in config.toml first (see the README), so the other device can reach this PC.")
        host = "<this-pc>.<tailnet>.ts.net"
    print("On the other device, in PowerShell:")
    print(f"  Invoke-RestMethod -Method Post -Uri https://{host}/api/pair -ContentType application/json "
          f"""-Body '{{"code":"{code}","device_name":"laptop"}}'""")


def show_devices(cfg: Config) -> None:
    from .engine.devices import list_devices

    store = Store(cfg.db_path)
    try:
        rows = list_devices(store)
    finally:
        store.close()
    if not rows:
        print("No devices are paired. `assistant pair` connects one.")
    for r in rows:
        state = f"revoked {r['revoked_at'][:16]}" if r["revoked_at"] else f"last seen {(r['last_seen_at'] or 'never')[:16]}"
        print(f"{r['id']:>3}  {r['name']:<20} paired {r['created_at'][:16]}  {state}")


def revoke_device(cfg: Config, device_id: int) -> None:
    from .engine.devices import revoke

    store = Store(cfg.db_path)
    try:
        if not revoke(store, device_id, datetime.now(timezone.utc)):
            raise AssistantError(f"No device {device_id} to revoke. `assistant devices` lists them.")
    finally:
        store.close()
    print(f"Revoked device {device_id}. Its key stopped working at once.")


def _writes_mail_state(args: argparse.Namespace) -> bool:
    """Commands that change the queue or the rankings, which the engine owns while it runs."""
    return (args.command in ("sync", "rescore")
            or (args.command == "filter" and not args.dry_run)
            or (args.command == "extract" and not args.estimate))


@contextmanager
def _owning_the_mail(args: argparse.Namespace, cfg: Config) -> Iterator[None]:
    """A command that writes holds engine.lock for its whole run, so an engine (or
    another writer) started meanwhile is refused instead of racing it."""
    if not _writes_mail_state(args):
        yield
        return
    from .engine.lock import EngineLock, lock_path

    lock = EngineLock(lock_path(cfg), role=args.command)
    lock.acquire(wait_seconds=0)
    try:
        yield
    finally:
        lock.release()


def _run(args: argparse.Namespace) -> None:
    if args.command == "api-schema":  # needs no config.toml: the contract is the same on every PC
        write_api_schema()
        return
    cfg = load_config(args.config)
    configure_kinds(cfg.kinds)  # the owner's own senders, from config.toml [kinds]
    with _owning_the_mail(args, cfg):
        _dispatch(args, cfg)


def _dispatch(args: argparse.Namespace, cfg: Config) -> None:
    if args.command == "auth":
        check_gmail(cfg)

    elif args.command == "sync":
        new, skipped = sync_mail(cfg, args.days)
        print(f"Sync done: {new} new, {skipped} skipped (already stored or not kept).")

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
        print("by status:")
        for row in store.counts_by_status():
            print(f"  {row['status']:<12}{row['n']:>6}")
        store.close()

    elif args.command == "filter":
        run_filter(cfg, dry_run=args.dry_run)

    elif args.command == "costs":
        show_costs(cfg)

    elif args.command == "opportunities":
        show_opportunities(cfg, args.top)

    elif args.command == "rescore":
        rescore(cfg)

    elif args.command == "set-key":
        if args.kind == "gmail":
            set_gmail_password(cfg, from_stdin=args.stdin)
        else:
            set_key(args.kind, from_stdin=args.stdin)

    elif args.command == "llm" and args.llm_command == "smoke":
        llm_smoke(cfg, shape=args.shape, record=args.record, email=args.email)

    elif args.command == "extract":
        extract(cfg, max_calls=args.max_calls, estimate=args.estimate)

    elif args.command == "pair":
        pair(cfg)

    elif args.command == "devices":
        if args.devices_command == "revoke":
            revoke_device(cfg, args.id)
        else:
            show_devices(cfg)

    elif args.command == "engine":
        from .engine.runtime import run_engine

        run_engine(cfg, dev=args.dev)

    elif args.command == "links":
        for row in link_audit(cfg, args.days):
            verdicts = ", ".join(f"{reason} {n}" for reason, n in row["verdicts"].most_common())
            print(f"{row['links']:>5}  {row['host']:<36} {verdicts}")
            for label, key in (("dropped params", "dropped_params"), ("kept params", "kept_params")):
                if row[key]:
                    print(f"       {label}: " + ", ".join(f"{k}({v})" for k, v in row[key].most_common()))


if __name__ == "__main__":
    main()
