"""The extraction run: queued emails -> Haiku 5.5 -> deduped, scored opportunities.

Per email: the free pre-filter, then the cap check, the model call, the
ledger update, fixes in code, and finally the email's opportunities saved
together with its queue status, in one short transaction. No transaction is
ever held open across a network call.

When things go wrong:

| What happened                        | What we do                                         |
|--------------------------------------|----------------------------------------------------|
| Refusal                              | mark the email `refused`; no retry (no fallback)   |
| Reply cut off (max_tokens)           | one retry with twice the room, then `invalid`      |
| JSON that doesn't fit                | one retry, then `invalid`                          |
| A 4xx other than 408/409/429         | stop the run, leave the queue untouched (raise):   |
|                                      | our request, key or account is wrong, not the mail |
| 408/409/429/5xx, timeout, a cap      | stop the run; the next run tries again             |
| Our own code crashes on an email     | after 3 runs, mark it `error` so it can't block    |
|                                      | the queue for good                                  |
| Every run                            | at most 60 model calls                             |

Why stop instead of marking emails failed? An early draft marked the whole
queue `error`, and so deleted the bodies, after a few hours of an outage or a
bad request shape. An email is only ever marked when its own reply was bad.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from .costs import TIMEZONE, Caps
from .errors import AssistantError, CapReached
from .extraction import email_for_model, extraction_request, parse_extraction, sanitize, to_opportunity
from .filters import prefilter
from .llm import LLM, LlmNoAnswer, LlmRejected, call_logged
from .profile import Profile
from .rank import score
from .store import Store

MAX_CALLS_PER_RUN = 60
MAX_ATTEMPTS = 3


@dataclass
class RunReport:
    counts: Counter = field(default_factory=Counter)  # emails by final status, and "new"/"merged" items
    calls: int = 0                                    # model calls actually sent
    stopped: str | None = None                        # why the run ended early, if it did
    fatal: AssistantError | None = None               # a 4xx that stopped everything: shown after the summary


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_extraction(
    store: Store, llm: LLM, profile: Profile, *, model: str, caps: Caps,
    clock: Callable[[], datetime] = utc_now, max_calls: int = MAX_CALLS_PER_RUN,
    log: Callable[[str], None] = print,
) -> RunReport:
    report = RunReport()
    today = clock().astimezone(TIMEZONE).date()
    for queued in store.queue():
        message_id = queued["message_id"]
        verdict = prefilter(queued["subject"], queued["sender_domain"], queued["source"],
                            json.loads(queued["labels"]), profile)
        if not verdict.keep:  # codes and security notices never reach the model
            if store.finish(message_id, "filtered", verdict.rule):
                report.counts["filtered"] += 1
            continue
        if queued["attempts"] >= MAX_ATTEMPTS:
            if store.finish(message_id, "error", f"gave up after {MAX_ATTEMPTS} runs"):
                report.counts["error"] += 1
                log(f"  error      gave up after {MAX_ATTEMPTS} runs  [{queued['source']}] {queued['subject'][:60]}")
            continue
        if report.calls >= max_calls:
            report.stopped = f"the limit of {max_calls} model calls per run"
            break
        row = store.claim(message_id)  # fresh: another run or the sweep may have taken it meanwhile
        if row is None:
            continue
        try:
            report.stopped = _extract_one(store, llm, profile, row, model=model, caps=caps, clock=clock,
                                          today=today, report=report, max_calls=max_calls, log=log)
        except LlmRejected as exc:  # not retryable: our request, key or account. Touch nothing more.
            report.stopped, report.fatal = f"error {exc.status} from Anthropic", exc
        except (KeyboardInterrupt, sqlite3.OperationalError):
            # Ctrl+C, or the database busy for 10 s: not the email's fault, so don't count it
            # (a reply already paid for is lost; the ledger keeps its worst-case estimate)
            _best_effort_unclaim(store, message_id)
            raise
        if report.stopped:
            break
    return report


def _best_effort_unclaim(store: Store, message_id: str) -> None:
    try:
        store.unclaim(message_id)
    except Exception:
        pass  # the database may be the problem; at worst the email counts one attempt


def _extract_one(store, llm, profile, row, *, model, caps, clock, today, report, max_calls, log) -> str | None:
    """Returns why the whole run must stop, or None to carry on. The email is claimed."""
    message_id = row["message_id"]
    email = email_for_model(row)
    request = extraction_request(email, profile, today, model=model)
    result = None
    for _ in range(2):  # the first try and at most one retry
        if report.calls >= max_calls:
            store.unclaim(message_id)  # not the email's fault: the next run starts it fresh
            return f"the limit of {max_calls} model calls per run"
        try:
            result = call_logged(store, llm, request, parse_extraction, caps=caps, now_utc=clock(),
                                 message_id=message_id)
        except CapReached as exc:  # nothing was sent
            store.unclaim(message_id)
            return f"the {exc.reason} (see `assistant costs`)"
        except LlmNoAnswer:
            report.calls += 1
            store.unclaim(message_id)
            return "no answer from Anthropic (timeout or lost connection)"
        except LlmRejected as exc:
            report.calls += 1
            store.unclaim(message_id)
            if exc.retryable:
                return f"Anthropic was busy (error {exc.status})"
            raise  # our request, key or account is wrong: stop, and touch nothing
        report.calls += 1
        if result.outcome == "max_tokens":
            request = replace(request, max_tokens=request.max_tokens * 2)
        elif result.outcome != "invalid":
            break

    subject = f"[{row['source']}] {row['subject'][:60]}"
    if result.outcome == "ok":
        flagged = result.value.instructions_to_ai_detected
        opps = [to_opportunity(item, email, flagged) for item in result.value.items]
        status = "extracted" if opps else "no_items"
        saved = store.save_extraction(message_id, opps, source=row["source"], flagged=flagged,
                                      scorer=lambda o: score(o, profile)[0], today=today.isoformat(),
                                      status=status, needs_action=result.value.needs_action,
                                      action_note=sanitize(result.value.action_note,
                                                           f"{email.subject}\n{email.body}", strict=flagged))
        if saved is None:  # another run finished it while our reply was on the way
            log(f"  skipped    already done by another run  {subject}")
            return None
        merged = sum(was_merged for _, was_merged in saved)
        report.counts[status] += 1
        report.counts["new"] += len(saved) - merged
        report.counts["merged"] += merged
        flag = "  [addressed the AI]" if flagged else ""
        log(f"  {status:<10} {len(opps)} item(s)  {subject}{flag}")
    elif result.outcome == "refused":
        if store.finish(message_id, "refused", result.reply.refusal_category or "no category"):
            report.counts["refused"] += 1
            log(f"  refused    ({result.reply.refusal_category or 'no category'})  {subject}")
    elif store.finish(message_id, "invalid", result.outcome):  # cut off twice, or JSON that didn't fit twice
        report.counts["invalid"] += 1
        log(f"  invalid    ({result.outcome})  {subject}")
    return None
