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
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
    """Shared by every worker of a run: change it only through the methods (they lock)."""
    counts: Counter = field(default_factory=Counter)  # emails by final status, and "new"/"merged" items
    calls: int = 0                                    # model calls actually sent
    stopped: str | None = None                        # why the run ended early, if it did
    stop_kind: str | None = None                      # cap_daily, busy, no_answer, call_limit or fatal
    fatal: AssistantError | None = None               # a 4xx that stopped everything: shown after the summary
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def take_call(self, max_calls: int) -> bool:
        """Reserve one model call before sending it; False once the run's limit is reached."""
        with self.lock:
            if self.calls >= max_calls:
                return False
            self.calls += 1
            return True

    def give_back_call(self) -> None:
        """The reserved call was never sent (a cap stopped it)."""
        with self.lock:
            self.calls -= 1

    def count(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.counts[key] += n

    def stop(self, kind: str, reason: str, fatal: AssistantError | None = None) -> None:
        """The first reason stands, except that a fatal error replaces a passing one."""
        with self.lock:
            if self.stop_kind is None or (kind == "fatal" and self.stop_kind != "fatal"):
                self.stop_kind, self.stopped = kind, reason
            if fatal is not None and self.fatal is None:
                self.fatal = fatal


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_extraction(
    store: Store, llm: LLM, profile: Profile, *, model: str, caps: Caps,
    clock: Callable[[], datetime] = utc_now, max_calls: int = MAX_CALLS_PER_RUN,
    log: Callable[[str], None] = print, workers: int = 1,
) -> RunReport:
    """workers=1 runs inline on `store`. More run the model calls in parallel threads,
    each with its own connection (sqlite3 connections stay in their thread); this
    thread still reads the queue and applies the pre-filter and give-up rules."""
    report = RunReport()
    one = dict(llm=llm, profile=profile, model=model, caps=caps, clock=clock,
               today=clock().astimezone(TIMEZONE).date(), report=report, max_calls=max_calls, log=log)
    stop = threading.Event()  # set by the first worker that stops the run: the rest do nothing
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") if workers > 1 else None
    futures = []
    try:
        for queued in store.queue():
            if stop.is_set():
                break
            message_id = queued["message_id"]
            verdict = prefilter(queued["subject"], queued["sender_domain"], queued["source"],
                                json.loads(queued["labels"]), profile)
            if not verdict.keep:  # codes and security notices never reach the model
                if store.finish(message_id, "filtered", verdict.rule):
                    report.count("filtered")
                continue
            if queued["attempts"] >= MAX_ATTEMPTS:
                if store.finish(message_id, "error", f"gave up after {MAX_ATTEMPTS} runs"):
                    report.count("error")
                    log(f"  error      gave up after {MAX_ATTEMPTS} runs  "
                        f"[{queued['source']}] {queued['subject'][:60]}")
                continue
            if report.calls >= max_calls:
                report.stop("call_limit", f"the limit of {max_calls} model calls per run")
                break
            if pool is None:
                if _claim_and_extract(store, message_id, **one):
                    break
            else:
                futures.append(pool.submit(_in_worker, store.db_path, message_id, stop, one))
    except BaseException:
        stop.set()
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        raise
    if pool is not None:
        pool.shutdown(wait=True)
        for future in futures:
            future.result()  # our own code crashed in a worker: raise it here, as inline would
    return report


def _in_worker(db_path, message_id: str, stop: threading.Event, one: dict) -> None:
    if stop.is_set():
        return
    store = Store(db_path)  # per task: a connection can't be closed from another thread
    try:
        if _claim_and_extract(store, message_id, **one):
            stop.set()
    except BaseException:
        stop.set()
        raise
    finally:
        store.close()


def _claim_and_extract(store: Store, message_id: str, *, report: RunReport, **one) -> bool:
    """Returns True when the whole run must stop."""
    row = store.claim(message_id)  # fresh: another run or the sweep may have taken it meanwhile
    if row is None:
        return False
    try:
        stopped = _extract_one(store, row=row, report=report, **one)
    except LlmRejected as exc:  # not retryable: our request, key or account. Touch nothing more.
        report.stop("fatal", f"error {exc.status} from Anthropic", exc)
        return True
    except (KeyboardInterrupt, sqlite3.OperationalError):
        # Ctrl+C, or the database busy for 10 s: not the email's fault, so don't count it
        # (a reply already paid for is lost; the ledger keeps its worst-case estimate)
        _best_effort_unclaim(store, message_id)
        raise
    if stopped:
        report.stop(*stopped)
        return True
    return False


def _best_effort_unclaim(store: Store, message_id: str) -> None:
    try:
        store.unclaim(message_id)
    except Exception:
        pass  # the database may be the problem; at worst the email counts one attempt


def _extract_one(store, llm, profile, row, *, model, caps, clock, today, report, max_calls,
                 log) -> tuple[str, str] | None:
    """Returns (stop_kind, why) when the whole run must stop, or None to carry on. The email is claimed."""
    message_id = row["message_id"]
    email = email_for_model(row)
    request = extraction_request(email, profile, today, model=model)
    result = None
    for _ in range(2):  # the first try and at most one retry
        if not report.take_call(max_calls):
            store.unclaim(message_id)  # not the email's fault: the next run starts it fresh
            return "call_limit", f"the limit of {max_calls} model calls per run"
        try:
            result = call_logged(store, llm, request, parse_extraction, caps=caps, now_utc=clock(),
                                 message_id=message_id)
        except CapReached as exc:  # nothing was sent
            report.give_back_call()
            store.unclaim(message_id)
            # Extraction is essential, so only the daily cap can stop it (costs.allowed)
            return "cap_daily", f"the {exc.reason} (see `assistant costs`)"
        except LlmNoAnswer:
            store.unclaim(message_id)
            return "no_answer", "no answer from Anthropic (timeout or lost connection)"
        except LlmRejected as exc:
            store.unclaim(message_id)
            if exc.retryable:
                return "busy", f"Anthropic was busy (error {exc.status})"
            raise  # our request, key or account is wrong: stop, and touch nothing
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
        report.count(status)
        report.count("new", len(saved) - merged)
        report.count("merged", merged)
        flag = "  [addressed the AI]" if flagged else ""
        log(f"  {status:<10} {len(opps)} item(s)  {subject}{flag}")
    elif result.outcome == "refused":
        if store.finish(message_id, "refused", result.reply.refusal_category or "no category"):
            report.count("refused")
            log(f"  refused    ({result.reply.refusal_category or 'no category'})  {subject}")
    elif store.finish(message_id, "invalid", result.outcome):  # cut off twice, or JSON that didn't fit twice
        report.count("invalid")
        log(f"  invalid    ({result.outcome})  {subject}")
    return None
