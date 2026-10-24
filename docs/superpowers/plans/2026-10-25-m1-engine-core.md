# M1 Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A long-running engine (`assistant engine`) that checks Gmail every 2 minutes through its change feed, reads new mail with 4 extraction workers, and keeps everything ranked unattended. No terminal commands are needed once it runs.

**Architecture:** A wall-clock scheduler runs four jobs in one background loop: sweep, check_mail, extract and profile_watch. check_mail uses Gmail's `history.list` from a stored bookmark, falling back to the existing search sync. extract reuses the Step 8 pipeline with a thread pool. Jobs publish events to an in-process bus that M2 will expose. While the engine runs it is the only writer, and an OS-level lock file keeps a second engine or a writing CLI command out.

**Tech Stack:** Python 3.12 (venv `.venv`), sqlite3, google-api-python-client (`users.history.list`), the existing anthropic adapter (`llm.py`), `concurrent.futures.ThreadPoolExecutor`, `msvcrt` / `fcntl` file locks, `logging.handlers.RotatingFileHandler`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-desktop-app-redesign-design.md` (sections 3, 4, 8, 9, 10 and 11/M1).

**Branch:** keep working on `phase-1`, which holds Steps 1–8 and the spec.

## Global Constraints

- Gmail scope stays `gmail.readonly`. Nothing sends, deletes or modifies mail.
- Mail bodies and API keys never reach logs. Log type names, ids and `vault.redact` output only.
- Tests stay offline. `tests/conftest.py` blocks the network, and no test calls a real API.
- **Timings:**
  - `check_mail` runs every **2 min**; while offline it backs off to **5 min**, then **15 min**.
  - `extract` runs right after `check_mail` adds mail, and every **1 min** while the queue isn't empty.
  - `sweep` runs at start-up and every **1 h**.
  - `profile_watch` runs every **30 s**.
  - The scheduler polls every **15 s**. A wake is a clock jump of **more than 3 min**.
- **Extraction:**
  - **4 workers** by default (`[engine] extract_workers`, 1–8).
  - At most **60 model calls a run**.
  - Every Step 8 rule stays: claim, guarded save, uncounted attempts on transient failures, and give up after 3 runs.
- **Fallback search sync:** `days = min(days since last good check, 7)`, rounded up to at least 1; it is 7 when there is no good check yet. The history bookmark is taken from `getProfile` **before** the search runs.
- **History feed:**
  - `historyTypes=["messageAdded"]`, `maxResults=500`, following `nextPageToken`.
  - An HTTP 404 means the bookmark expired: run the fallback.
  - Skip messages labelled `SPAM`, `TRASH`, `CHAT`, `SENT` or `DRAFT`.
- **State keys:** `gmail_history_id`, `gmail_address`, `last_ok_check_at` (ISO, UTC), `last_check_error`.
- **Events:** `items_new`, `status`, `ranking_changed`.
- **Logs:** `<data dir>/logs/engine.log`, rotating at 1 MB with 4 backups (5 files). The data directory is `cfg.db_path.parent` until M4 moves it.
- `action_note` holds at most 120 characters and passes through `extraction.sanitize`, strictly when the email was flagged.
- The only paid API calls in M1 are the smoke re-recording (Task 11) and the live check (Task 12). Both need the owner's explicit approval at that time.

## Review Focus

1. **The laptop sleeps or the clock is set backwards.** Jobs must still run once on the next tick, never stall, and never burst. Test: Task 6, `test_a_clock_that_goes_backwards_still_runs_jobs`.
2. **A message deleted, or moved to Spam, between the feed listing it and the fetch.** Skip it and keep going; the bookmark still advances. Test: Task 4, `test_a_message_deleted_before_fetch_is_skipped_and_the_bookmark_still_advances`.
3. **Mail arriving while a fallback search runs.** Nothing is missed, because the bookmark is taken before the search. A duplicate fetch is harmless, since `has()` skips it. Test: Task 4, `test_the_fallback_takes_its_bookmark_before_searching`.
4. **The owner saves a broken `profile.toml` while the engine runs.** Keep the old profile, report the problem, don't crash, and recover after the next good save. Test: Task 8, `test_a_broken_profile_edit_keeps_the_old_profile_until_fixed`.
5. **The engine is killed: a crash, Task Manager or a power cut.** No stale lock may block the next start. Test: Task 7, `test_the_lock_frees_when_its_holder_process_dies`.

---

### Task 1: Store: migrations v6–v7, state, sightings, needs-action columns

**Files:**
- Modify: `src/assistant/store.py`
- Test: `tests/test_store.py` (append), `tests/test_opportunities.py` (append)

**Interfaces:**
- Consumes: the existing `Migration`, `MIGRATIONS`, `Store._immediate`, `Store._save_opportunity` and `Store.save_extraction`.
- Produces:
  - `Store.db_path: Path`, set in `__init__`.
  - `Store.get_state(key: str) -> str | None`
  - `Store.set_state(key: str, value: str | None) -> None`, committed. `None` deletes the key.
  - `Store.save_extraction(..., needs_action: bool = False, action_note: str | None = None)`. It writes `mail_items.needs_action` and `action_note` in the same transaction, before `_finish`.
  - `_save_opportunity` does `INSERT OR IGNORE INTO sightings (opportunity_id, message_id)` on both the insert path and the merge path.

**Migration text.** Frozen, written out like v4 and v5:

```sql
-- v6: actions and sightings
ALTER TABLE opportunities ADD COLUMN starred INTEGER NOT NULL DEFAULT 0;
CREATE TABLE sightings (
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    message_id TEXT NOT NULL,
    seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (opportunity_id, message_id));
CREATE INDEX idx_sightings_message ON sightings(message_id);
INSERT INTO sightings (opportunity_id, message_id, seen_at)
    SELECT id, first_message_id, first_seen_at FROM opportunities;
CREATE TABLE item_actions (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    action TEXT NOT NULL CHECK (action IN ('star', 'unstar', 'dismiss', 'restore', 'applied')),
    at TEXT NOT NULL DEFAULT (datetime('now')));
-- v7: needs-action, digests and state
ALTER TABLE mail_items ADD COLUMN needs_action INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mail_items ADD COLUMN action_note TEXT;
CREATE TABLE digests (
    digest_date TEXT PRIMARY KEY, text TEXT NOT NULL, html TEXT NOT NULL, item_ids TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), sent_at TEXT, send_error TEXT);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
def test_v6_v7_upgrade_a_v5_database_and_keep_its_rows(tmp_path):
    # Build a v5 database (MIGRATIONS[:5]) holding one opportunity, then reopen it with every migration
    ...
    assert schema_version(store.conn) == 7
    assert store.conn.execute("SELECT opportunity_id, message_id FROM sightings").fetchall() == [(1, "m1")]
    assert store.conn.execute("SELECT starred FROM opportunities").fetchone()[0] == 0

def test_state_round_trips_and_none_deletes(tmp_path):
    store = Store(tmp_path / "a.db")
    assert store.get_state("gmail_history_id") is None
    store.set_state("gmail_history_id", "100"); store.set_state("gmail_history_id", "120")
    assert store.get_state("gmail_history_id") == "120"
    store.set_state("gmail_history_id", None)
    assert store.get_state("gmail_history_id") is None

def test_item_actions_accept_only_known_actions(tmp_path):
    # Inserting action 'like' raises sqlite3.IntegrityError

# tests/test_opportunities.py
def test_save_extraction_records_every_sighting_and_the_needs_action_note(tmp_path):
    # m1 saves items A and B with needs_action=True, action_note="Register by 15 Nov"; m2 saves A again
    assert set(store.conn.execute("SELECT opportunity_id, message_id FROM sightings")) == {(1, "m1"), (2, "m1"), (1, "m2")}
    assert tuple(store.conn.execute("SELECT needs_action, action_note FROM mail_items WHERE message_id='m1'").fetchone()) == (1, "Register by 15 Nov")
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `.venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_store.py tests/test_opportunities.py`
Expected: the 4 new tests FAIL (no table `sightings`, no `get_state`).

- [ ] **Step 3: Implement**

Add `_v6_actions_and_sightings` and `_v7_needs_action_digests_state` with the SQL above, and append them to `MIGRATIONS` as `"v6: actions and sightings"` and `"v7: needs-action, digests and state"`. Then add `db_path`, `get_state`/`set_state`, the sightings insert, and the `save_extraction` parameters.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q -p no:cacheprovider`
Expected: everything passes (478 + 4).

- [ ] **Step 5: Commit**: `git commit -m "Add sightings, item actions, state and digests tables"`

---

### Task 2: Extraction returns needs-action and an action note

**Files:**
- Modify: `src/assistant/extraction.py`, `src/assistant/pipeline.py`
- Modify fixtures: `tests/fixtures/llm/synthetic_ok.json` and `synthetic_thinking_then_text.json`. Add `"needs_action": false, "action_note": null` to their JSON text.
- Test: `tests/test_extraction.py`, `tests/test_pipeline.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Store.save_extraction(..., needs_action=, action_note=)` from Task 1.
- Produces:
  - `Extraction.needs_action: bool` and `Extraction.action_note: str | None`. Both are required, and the note is nullable.
  - `action_note` is shortened to 120 characters with "…" by a validator, like `Item._shorten`.
  - `pipeline._extract_one` passes `needs_action` and `sanitize(action_note, text, strict=flagged)` to `save_extraction`.

**Prompt.** Add these two lines, verbatim, to the end of the `# Fields` list in `SYSTEM_TEMPLATE`:

```
- needs_action (for the whole email): true only if it asks the candidate to do something by a date: register, pay, submit, confirm or reply. Newsletters and job alerts are false.
- action_note: what to do and by when, in at most 120 characters ("Register for semester 2 courses by 15 Nov"); null when needs_action is false.
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extraction.py
def test_the_schema_requires_needs_action_and_its_note():
    schema = output_schema(Extraction)
    assert {"needs_action", "action_note"} <= set(schema["required"])

def test_a_long_action_note_is_shortened_not_refused():
    # parse_extraction(text with needs_action true and a 300-character note) -> 120 characters, ending "…"

def test_the_prompt_explains_needs_action(profile):
    assert "needs_action (for the whole email)" in system_prompt(profile)

# tests/test_pipeline.py: the reply() helper adds needs_action and action_note
# (default False / None) to its JSON text
def test_an_email_that_needs_action_is_marked_with_a_clean_note(store):
    add_mail(store, "m1")
    run(store, FakeLLM(reply([], needs_action=True, note="Pay at pay-desk.com by Friday")))
    assert (mail(store)["needs_action"], mail(store)["action_note"]) == (1, "Pay at by Friday")
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_extraction.py tests/test_pipeline.py`

- [ ] **Step 3: Implement**

Add the fields, the validator, the prompt lines and the pipeline call. Then update both synthetic fixtures.

The recorded live fixture predates these fields. Mark `test_the_recorded_live_reply_reads_and_prices_as_expected` with `@pytest.mark.xfail(strict=True, reason="recorded before needs_action; re-recorded in Task 11")`. Change nothing else in that fixture.

- [ ] **Step 4: Run the full suite**

Expected: all pass, with 1 xfailed.

- [ ] **Step 5: Commit**: `git commit -m "Extract needs-action and an action note per email"`

---

### Task 3: Ingest module and a shared Gmail fake

**Files:**
- Create: `src/assistant/ingest.py`, `tests/fake_gmail.py`
- Modify: `src/assistant/cli.py` (`sync_mail` delegates to `ingest.search_sync`), `tests/test_cli.py` (imports `FakeGmail` from `fake_gmail`)
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `gmail_client.get_message` and `iter_message_ids`, `parser.build_mail_item` and `is_outgoing`, `cli.build_query`.
- Produces:
  - `ingest.SKIP_LABELS = frozenset({"SPAM", "TRASH", "CHAT", "SENT", "DRAFT"})`
  - `ingest.ingest_ids(store: Store, service, ids: Iterable[str], cfg: Config, my_addresses: tuple[str, ...], log: Callable[[str], None] = print) -> tuple[int, int]`
    - Returns `(new, skipped)`.
    - Ids already stored are counted as skipped.
    - Messages it won't keep are skipped: a 404 on fetch (`googleapiclient.errors.HttpError` with `resp.status == 404`), any label in `SKIP_LABELS`, `is_outgoing`, or a parse error. Each is logged by message id and reason only.
    - It commits every 50 messages and at the end.
  - `ingest.search_sync(store, service, cfg, days: int, my_addresses, log=print) -> tuple[int, int]` runs `iter_message_ids(service, build_query(cfg.gmail_query, days), cfg.max_messages)`, then `ingest_ids`.
  - `cli.sync_mail(cfg, days)` keeps its signature, return value and closing sweep.
  - **`tests/fake_gmail.py`:**
    - `FakeGmail(messages, profile, history_id="100")` is moved from `test_cli.py`.
    - `getProfile` returns `{"emailAddress", "historyId"}`.
    - `delete(msg_id)` makes `get` raise a 404 `HttpError`.
    - `calls: list[str]` records the methods called in order.
    - Task 4 adds history support.

- [ ] **Step 1: Write the failing tests**

```python
def test_ingest_skips_stored_spam_trash_chat_and_deleted_messages(tmp_path):
    # Ids: new, already-stored, a SPAM-labelled one, a CHAT-labelled one, and one deleted before fetch
    assert ingest_ids(store, fake, ids, cfg, ("me@x.com",), log=lines.append) == (1, 4)
    assert store.has("new") and not store.has("spam1")
    assert all("Hackathon" not in line for line in lines)  # logs carry ids, never subjects or bodies
```

- [ ] **Step 2: Run to confirm it fails** (no module `assistant.ingest`).
- [ ] **Step 3: Implement `ingest.py`, move `FakeGmail`, and point `sync_mail` at it.**
- [ ] **Step 4: Run the full suite.** The existing sync tests in `test_cli.py` must still pass unchanged.
- [ ] **Step 5: Commit**: `git commit -m "Move mail ingestion into its own module"`

---

### Task 4: Gmail change feed and check_mail

**Files:**
- Create: `src/assistant/engine/__init__.py` (a docstring only), `src/assistant/engine/gmail_feed.py`
- Modify: `tests/fake_gmail.py` (history support)
- Test: `tests/test_gmail_feed.py`

**Interfaces:**
- Consumes:
  - `ingest.ingest_ids` and `ingest.search_sync` (Task 3);
  - `Store.get_state` and `Store.set_state` (Task 1);
  - `datetime` values that are UTC-aware (naive ones are refused, as in `costs.local_period`).
- Produces:
  - `class HistoryExpired(Exception)`
  - `gmail_feed.fetch_new_ids(service, start_history_id: str) -> tuple[list[str], str]`. It returns the added message ids, de-duplicated in first-seen order and skipping entries whose `labelIds` hit `ingest.SKIP_LABELS`, plus the last page's top-level `historyId`. A 404 raises `HistoryExpired`.
  - `gmail_feed.profile(service) -> tuple[str, str]`: the lower-cased address and the `historyId`.
  - `@dataclass(frozen=True) class CheckResult: new: int; mode: Literal["history", "search"]`
  - `gmail_feed.check_mail(store: Store, service, cfg: Config, *, now_utc: datetime, log=print) -> CheckResult`
    1. With a bookmark: try the feed, then `ingest_ids`.
    2. With no bookmark, or after `HistoryExpired`: take `profile()` **first**, then `search_sync(days=...)` per Global Constraints.
    3. On success, set `gmail_history_id`, `gmail_address` and `last_ok_check_at`, and clear `last_check_error`. `my_addresses` is `(gmail_address, *cfg.university_addresses)`.
- **`FakeGmail` history support:**
  - `history_pages: list[dict]`, returned in order, with `pageToken` mapping to the index;
  - `expire_history()` makes `history().list` raise a 404;
  - `calls` records `"history.list"`, `"getProfile"` and `"messages.list"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_feed_returns_added_ids_in_order_without_duplicates_and_the_new_bookmark():
    # Two pages (nextPageToken), id "b" in both, one entry labelled SPAM
    assert fetch_new_ids(fake, "100") == (["a", "b", "c"], "130")

def test_an_expired_bookmark_raises_history_expired():  # fake.expire_history()

def test_check_mail_uses_the_feed_when_it_has_a_bookmark(tmp_path):
    assert check_mail(store, fake, cfg, now_utc=NOW) == CheckResult(new=2, mode="history")
    assert "messages.list" not in fake.calls and store.get_state("gmail_history_id") == "130"

def test_the_fallback_takes_its_bookmark_before_searching(tmp_path):   # Review Focus 3
    # Bookmark set, history expired, last_ok_check_at 3 days ago
    result = check_mail(store, fake, cfg, now_utc=NOW)
    assert result.mode == "search" and fake.queries == ["newer_than:3d -in:chats -in:spam -in:trash -in:sent -in:drafts"]
    assert fake.calls.index("getProfile") < fake.calls.index("messages.list")

def test_a_first_check_without_a_bookmark_searches_7_days(tmp_path):

def test_a_message_deleted_before_fetch_is_skipped_and_the_bookmark_still_advances(tmp_path):  # Review Focus 2
```

The expected query follows the config's `gmail.query` template, which defaults to `newer_than:{days}d -in:chats -in:spam -in:trash`. Use the `Config` the test builds.

- [ ] **Step 2: Run to confirm it fails.**
- [ ] **Step 3: Implement `gmail_feed.py`** and the `FakeGmail` history support.
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit**: `git commit -m "Check Gmail through its change feed, with a search fallback"`

---

### Task 5: Extraction with several workers

**Files:**
- Modify: `src/assistant/pipeline.py`
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `Store.db_path` (Task 1), the existing `Store.claim`, `unclaim`, `save_extraction` and `finish`.
- Produces:
  - `run_extraction(store, llm, profile, *, model, caps, clock=utc_now, max_calls=60, log=print, workers: int = 1) -> RunReport`. With `workers == 1` it behaves exactly as now, inline on the caller's store.
  - `RunReport` gains `stop_kind: str | None`, one of `"cap_daily"`, `"busy"`, `"no_answer"`, `"call_limit"` or `"fatal"`, alongside `stopped`.
  - `RunReport.take_call(max_calls: int) -> bool` and `RunReport.give_back_call() -> None`, both thread-safe. A call slot is taken before each model call and given back on `CapReached`.

**Approach:**
- The calling thread reads the queue and does the pre-filter and give-up steps, as now.
- Each kept row goes to a `ThreadPoolExecutor(max_workers=workers)` task. The task opens its own `Store(store.db_path)`, once per thread, and checks a shared `threading.Event` "stop". If the event is set, the task does nothing. Otherwise it claims the row and runs `_extract_one`.
- The first stop reason, or a fatal `LlmRejected`, sets the event.
- All worker stores are closed in `finally`.
- The LLM object is shared, which is safe because the httpx2 client is thread-safe. The `FakeLLM` used here needs a lock.

- [ ] **Step 1: Write the failing tests.** Add a `SameReplyLLM(reply_dict)` fake: thread-safe, the same reply every time, counting calls.

```python
def test_four_workers_extract_every_email_exactly_once(store):
    # 12 queued emails
    report, _ = run(store, SameReplyLLM(reply([item()])), workers=4)
    assert report.calls == 12 and report.counts["extracted"] == 12
    assert outcomes(store) == ["ok"] * 12
    assert {mail(store, f"m{n}")["status"] for n in range(12)} == {"extracted"}

def test_the_call_limit_holds_across_workers(store):
    # 12 emails, max_calls=5 -> exactly 5 calls, stop_kind "call_limit",
    # 7 emails still 'new' with attempts 0

def test_a_daily_cap_stops_every_worker_before_any_call(store):
    # Caps(5.0, 0.0001), workers=4 -> 0 calls, stop_kind "cap_daily",
    # every email 'new' with attempts 0

def test_a_bad_request_in_one_worker_stops_the_others_and_marks_no_email(store):
    # The first reply raises LlmRejected(400) -> report.fatal is set, stop_kind "fatal",
    # no email 'invalid' or 'error', and report.calls <= 4
```

- [ ] **Step 2: Run to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** The existing pipeline tests, with `workers=1`, must pass unchanged.
- [ ] **Step 5: Commit**: `git commit -m "Run extraction on several workers"`

---

### Task 6: Wall-clock scheduler

**Files:**
- Create: `src/assistant/engine/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces:
  - `JobFn = Callable[[datetime], timedelta | None]`. A job may return a delay that replaces its normal interval for the next run only.
  - `class Scheduler`:
    - `__init__(self, clock: Callable[[], datetime], *, wake_gap: timedelta = timedelta(minutes=3), on_error: Callable[[str, BaseException], None] = ...)`
    - `add(name: str, every: timedelta, fn: JobFn) -> None`. The first run is due immediately, in registration order.
    - `run_soon(name: str) -> None`, thread-safe.
    - `tick() -> list[str]`. It runs every job that is due, once each, in registration order, and returns their names.
    - `next_due(name: str) -> datetime`
    - `run_forever(stop: threading.Event, poll_seconds: float = 15.0) -> None` repeats `tick()` and then `stop.wait(poll_seconds)` until `stop` is set.
  - **Rules:**
    - After a run, the next due time is `now + (returned delay or every)`. It is never `due + every`, so there are no bursts after sleep.
    - An `Exception` from a job goes to `on_error(name, exc)`, and the next due time is `now + every`. A `BaseException` such as `KeyboardInterrupt` propagates.
    - If `now - last_tick > wake_gap` or `now < last_tick`, every job is due now.

- [ ] **Step 1: Write the failing tests** (a fake clock with an `advance(timedelta)` method)

```python
def test_jobs_run_at_once_then_at_their_interval():
def test_a_job_can_ask_for_a_different_next_delay():
def test_run_soon_runs_a_job_on_the_next_tick():
def test_a_failing_job_is_reported_and_the_others_still_run():
def test_waking_from_sleep_runs_each_job_once_not_once_per_missed_interval():
    # Every 2 min, clock +2 h -> tick() == ["check"] once, and next_due is now + 2 min
def test_a_clock_that_goes_backwards_still_runs_jobs():   # Review Focus 1
    # Advance 10 min, tick, set the clock back 1 h -> the next tick runs every job
def test_run_forever_stops_when_the_event_is_set():
```

- [ ] **Step 2: Run to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit**: `git commit -m "Add a wall-clock scheduler that survives sleep"`

---

### Task 7: Engine lock and the CLI write guard

**Files:**
- Create: `src/assistant/engine/lock.py`
- Modify: `src/assistant/cli.py`, `src/assistant/errors.py`
- Test: `tests/test_engine_lock.py`

**Interfaces:**
- Produces:
  - `errors.EngineRunning(AssistantError)` with the message `"The Pocket Assistant engine is running. Use the dashboard, or stop the engine first."`
  - `lock.lock_path(cfg: Config) -> Path` returns `cfg.db_path.parent / "engine.lock"`.
  - `class EngineLock`:
    - `__init__(self, path: Path)`
    - `acquire() -> None`, which raises `EngineRunning` when the lock is held;
    - `release() -> None`;
    - context manager support.
  - `lock.engine_running(path: Path) -> bool`
- **Approach:**
  - Hold an OS-level, non-blocking, exclusive lock on the open file for the engine's whole life: `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` on Windows, `fcntl.flock(fd, LOCK_EX | LOCK_NB)` elsewhere. The OS drops it when the process dies, so there is never a stale lock.
  - Write the PID into the file for humans to read.
  - **Never use `os.kill` to test whether a PID is alive. On Windows it terminates the process.**
- **CLI guard.** These commands raise `EngineRunning` while `engine_running(lock_path(cfg))` is true:
  - `sync`;
  - `filter` without `--dry-run`;
  - `extract` without `--estimate`;
  - `rescore`.

  Every other command still works.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_second_lock_is_refused_while_the_first_is_held(tmp_path):
def test_the_lock_frees_when_its_holder_process_dies(tmp_path):   # Review Focus 5
    # subprocess.run([sys.executable, "-c", "<acquire the lock, print('held'), os._exit(0)>"])
    # then EngineLock(path).acquire() succeeds
def test_writing_commands_refuse_while_the_engine_runs(tmp_path):
    # Hold the lock; main([... "sync"]) -> SystemExit containing "engine is running"
def test_reading_commands_still_work_while_the_engine_runs(tmp_path):
    # Hold the lock; main([... "stats"]) succeeds; so does "extract --estimate"
```

- [ ] **Step 2: Run to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit**: `git commit -m "Allow one engine at a time, and keep CLI writers out while it runs"`

---

### Task 8: Event bus and the engine's jobs

**Files:**
- Create: `src/assistant/engine/events.py`, `src/assistant/engine/jobs.py`
- Test: `tests/test_engine_jobs.py`

**Interfaces:**
- Consumes:
  - `Scheduler.run_soon` (Task 6);
  - `gmail_feed.check_mail` (Task 4);
  - `run_extraction(..., workers=)` and `RunReport.stop_kind` (Task 5);
  - `Store.purge_stale_bodies`, `rescore_opportunities`, `get_state` and `set_state`;
  - `profile.load_profile`, `rank.score`.
- Produces:
  - **`events.py`:** `ITEMS_NEW = "items_new"`, `STATUS = "status"`, `RANKING_CHANGED = "ranking_changed"`.
    - `class EventBus` with `publish(kind: str, payload: dict) -> None`, `subscribe(max_queue: int = 100) -> queue.Queue` and `unsubscribe(q) -> None`. All are thread-safe.
    - Each event is `{"kind": ..., **payload}`. A full subscriber queue drops its own oldest event; other subscribers are unaffected.
  - **`jobs.py`:** each job is a callable class matching `JobFn`.
    - **`ProfileHolder`** holds the current profile and the file's modification time, behind a `threading.Lock`. It has `get() -> Profile`.
    - **`CheckMailJob(store, gmail: Callable[[], Any], cfg, bus, scheduler, log)`.** It builds the Gmail service lazily through `gmail()` and drops it after any failure.
      - **Success:** publishes `STATUS {"online": True, "last_ok_check_at": iso, "problem": None}`. When `new > 0` it calls `scheduler.run_soon("extract")`. Returns `None`.
      - **`GmailAuthError`, or google-auth `RefreshError`:** sets `last_check_error`, publishes `STATUS` with `"problem": "auth_lost"`, and returns 15 min.
      - **Offline** (`OSError`, `httplib2.HttpLib2Error`, `google.auth.exceptions.TransportError`, or an `HttpError` with status ≥ 500): publishes `"problem": "offline"` and returns 5 min after the first failure in a row, 15 min after later ones.
      - Anything else propagates to the scheduler.
    - **`ExtractJob(store, llm, holder, cfg, bus, log, workers)`.** When the queue is empty it returns `None`. Otherwise it calls `run_extraction(workers=workers)`.
      - It publishes `ITEMS_NEW {"new": n, "merged": m}` when `n + m > 0`.
      - It then returns a delay based on `stop_kind`:

        | `stop_kind` | Returns |
        |---|---|
        | `"cap_daily"` | The time until the next 00:00 Asia/Ho_Chi_Minh |
        | `"busy"` or `"no_answer"` | 5 min |
        | `"fatal"` | 60 min, and publishes `STATUS {"problem": "anthropic", "detail": str(report.fatal)}` |
        | `"call_limit"` or `None` | `None` |
    - **`SweepJob(store, log)`** runs `purge_stale_bodies()` and logs the counts.
    - **`ProfileWatchJob(store, cfg, holder, bus, log)`.**
      - When `cfg.profile_path`'s modification time changes, it runs `load_profile`.
      - On `ConfigError` it publishes `STATUS {"problem": "profile", "detail": message}` and keeps the old profile.
      - Otherwise it swaps the profile in, runs `rescore_opportunities(lambda o: score(o, profile)[0], today)`, and publishes `RANKING_CHANGED {"rescored": n}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_subscriber_gets_each_event():
def test_a_slow_subscriber_drops_its_own_oldest_event_only():
def test_new_mail_makes_extraction_run_at_once(tmp_path):   # Real Scheduler with a fake clock; next_due("extract") == now
def test_offline_backs_off_5_then_15_minutes_then_recovers(tmp_path):
def test_lost_gmail_access_is_reported_and_retried_in_15_minutes(tmp_path):
def test_new_items_are_announced(tmp_path):
def test_a_daily_cap_waits_until_midnight_in_hcmc(tmp_path):
    # now = 2026-10-25T10:00Z -> returns 7 h (17:00Z is 00:00 HCMC)
def test_a_busy_api_retries_in_5_minutes(tmp_path):
def test_a_profile_edit_rescores_and_announces_it(tmp_path):
def test_a_broken_profile_edit_keeps_the_old_profile_until_fixed(tmp_path):   # Review Focus 4
```

- [ ] **Step 2: Run to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit**: `git commit -m "Add the engine's jobs and event bus"`

---

### Task 9: `assistant engine`

**Files:**
- Create: `src/assistant/engine/runtime.py`, `src/assistant/engine/logs.py`
- Modify:
  - `src/assistant/config.py`: an `[engine]` section with `extract_workers`, an integer 1–8, default 4.
  - `src/assistant/cli.py`: the `engine` subcommand.
  - `config.example.toml`: a commented `[engine]` section.
- Test: `tests/test_engine_runtime.py`

**Interfaces:**
- Consumes: everything from Tasks 4–8, `AnthropicLLM.from_vault`, `gmail_client.build_service` and `get_credentials`.
- Produces:
  - `config.EngineConfig(extract_workers: int = 4)` and `Config.engine`.
  - `logs.setup_logging(log_dir: Path) -> logging.Logger`:
    - logger `"assistant.engine"`;
    - `RotatingFileHandler(log_dir / "engine.log", maxBytes=1_000_000, backupCount=4, encoding="utf-8")` plus a console handler;
    - a `logging.Filter` that runs `vault.redact` on each record's final message.
  - `runtime.Engine`:
    - `__init__(self, cfg: Config, *, clock, gmail: Callable[[], Any], llm: LLM, bus: EventBus, log: logging.Logger)`. It registers the jobs in this order: `sweep` (1 h), `check_mail` (2 min), `extract` (1 min), `profile_watch` (30 s). That order is also the wake catch-up order.
    - The scheduler's `on_error` (spec section 9) logs `"<job> failed: <ExceptionType>: <redacted message>"` and publishes `STATUS {"problem": "job_error", "detail": "<job>: <ExceptionType>"}`. The next run tries again.
    - Attributes `.scheduler`, `.bus` and `.holder`.
    - `run(stop: threading.Event, poll_seconds: float = 15.0) -> None`
  - `runtime.run_engine(cfg: Config) -> None` is the CLI entry. It:
    1. takes `EngineLock(lock_path(cfg))`;
    2. sets up logging in `cfg.db_path.parent / "logs"`;
    3. builds the real clock, the Gmail factory (`lambda: build_service(get_credentials())`) and `AnthropicLLM.from_vault()`;
    4. prints `Engine running. New mail is checked every 2 minutes. Press Ctrl+C to stop.`;
    5. turns Ctrl+C into a clean stop.
  - `assistant engine` calls `run_engine(cfg)`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("value", [0, 9, '"4"', "true"])
def test_engine_settings_refuse_nonsense(tmp_path, value):   # [engine] extract_workers = <value> -> ConfigError
def test_engine_settings_default_to_4_workers(tmp_path):
def test_one_engine_run_checks_mail_then_extracts_it(tmp_path):
    # FakeGmail feed: 2 new job-alert messages; FakeLLM replies; fake clock.
    # engine.scheduler.tick() twice -> opportunities saved; the bus saw "status" then "items_new"
def test_the_engine_log_never_holds_a_key_or_a_mail_body(tmp_path):
    # Run the tick above with logging to tmp_path; log FAKE_KEY through the logger.
    # Neither FAKE_KEY nor the fake message body appears in engine.log
def test_a_job_that_crashes_is_logged_and_announced_and_the_engine_keeps_going(tmp_path):
    # Make the sweep raise ValueError("x"); tick -> the log has "sweep failed: ValueError",
    # the bus has status with problem "job_error", and check_mail still ran
def test_a_second_engine_refuses_to_start(tmp_path):
    # Hold EngineLock; run_engine(cfg) raises EngineRunning before building anything
```

- [ ] **Step 2: Run to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit**: `git commit -m "Add the assistant engine command"`

---

### Task 10: Two-lens adversarial review of M1

The spec requires this for M1 (section 11, review policy).

- [ ] **Step 1: Run a two-agent review workflow** over the M1 diff (`git diff ca8602d..HEAD -- src tests`), with these lenses:
  1. **Gmail feed and ingest:** bookmark correctness, fallback gaps, label skipping, 404 handling, pagination, auth and offline classification, and a Gmail service used across threads, which must never happen.
  2. **Concurrency and scheduling:** worker stores, the call limit, stop propagation, claim and save under 4 workers, scheduler sleep and clock edge cases, the lock, and the jobs' backoff and events.

  Reviewers write probes only in the scratchpad, against temporary databases, with no network and no real keyring.
- [ ] **Step 2: Fix each confirmed finding** with a regression test, and mutation-check each fix.
- [ ] **Step 3: Run the full suite.**
- [ ] **Step 4: Commit**: `git commit -m "Fix M1 review findings"`

---

### Task 11: Re-record the smoke fixture and document the engine

- [ ] **Step 1: Ask the owner to approve one smoke call** (about $0.0006) and wait for a yes.
- [ ] **Step 2: Run** `.venv/Scripts/assistant.exe llm smoke --record tests/fixtures/llm/recorded_smoke_ok.json`
  Expected: `ok` with 2 items. The reply now includes `needs_action`.
- [ ] **Step 3: Update `test_the_recorded_live_reply_reads_and_prices_as_expected`**
  - Remove the `xfail` mark.
  - Change the expected usage and cost to the new recording's numbers, computed by hand from the price table.
  - Assert `needs_action is False` for this invented hackathon email.
- [ ] **Step 4: README.** Add a "Running the engine" section: `assistant engine`, what it does every 2 minutes, where the log is, and which commands refuse while it runs.
- [ ] **Step 5: Run the full suite.**
- [ ] **Step 6: Commit**: `git commit -m "Re-record the smoke reply and document the engine"`

---

### Task 12: Live check on the real mailbox

These steps need the owner's approval.

- [ ] **Step 1: The owner's task.** Confirm the Google OAuth app is published, which is decision D6, or accept a 7-day token for this check. Guide them through publishing if they choose to.
- [ ] **Step 2: Estimate.** Run `.venv/Scripts/assistant.exe extract --estimate` after a fresh `sync --days 7`. Then report the email count and the expected cost, and wait for approval.
- [ ] **Step 3: Start the engine.** Run `.venv/Scripts/assistant.exe engine` in a terminal tab the owner can see. Expected: the first ticks sweep, search-sync 7 days, and extract with 4 workers.
- [ ] **Step 4: Time a new email.** The owner sends themselves a test email with an opportunity in it.
  Expected: it appears in `assistant opportunities --top 20` within 2.5 minutes. Record the time from the log.
- [ ] **Step 5: Stop the engine with Ctrl+C.** Check that `engine.log` holds no mail body, and that `assistant costs` matches the run.
- [ ] **Step 6: Record the results** in the spec's section 11 under M1, then commit: `git commit -m "Record the M1 live check"`
