# M4 Part 1: Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Tauri 2 tray app on the PC that starts with Windows, runs the engine with the project's Python (handshake, restarts, graceful quit) and shows the M3 dashboard in its own instant window, signed in by itself.

**Architecture:** The engine gains `--parent-pid` and `--password-stdin` (READY line, parent watch, stop on end of stdin). A Rust shell in `app/src-tauri/` supervises it (settings discovery, restart policy, tray, single instance, autostart) and exposes `engine_connection` and `open_profile` commands. The dashboard detects Tauri and signs in through the command; outside Tauri nothing changes.

**Tech Stack:** Python 3.12 (engine), Rust 1.100 + Tauri 2.12 (shell), React 19 + Vite 8 + Vitest (dashboard).

**Spec:** `docs/superpowers/specs/2026-11-18-m4-desktop-app-design.md`

## Global Constraints

- Pins (exact): `tauri` =2.12.5 (features `tray-icon`, `image-png`), `tauri-build` =2.7.4, `tauri-plugin-autostart` =2.7.0, `tauri-plugin-single-instance` =2.5.5, `tauri-plugin-opener` =2.7.2, `tauri-plugin-window-state` =2.5.0, `serde` =1.0.229 (derive), `serde_json` =1.0.154, `getrandom` =0.4.3; npm `@tauri-apps/cli` 2.12.4 (dev). `Cargo.lock` committed. No other new dependencies.
- Rust toolchain: `$USERPROFILE/.cargo/bin` must be on PATH in shells (`export PATH="$USERPROFILE/.cargo/bin:$PATH"`).
- Handshake: password = 32 random bytes, hex (64 chars), written to the engine's stdin as one line; engine prints `READY port=<n>` (flushed); app waits ≤ 30 s for it.
- Parent watch every 5 s; Quit/Restart close stdin and wait ≤ 150 s before a kill; Windows shutdown is a hard stop.
- Restarts after 1, 5, 30 s (then 30 s); give up after 5 crashes within 10 minutes.
- Engine already running (engine.lock held) → the CLI exits with code **3**; the app shows "Engine already running in a terminal", does not count it as a crash, and retries every 10 s.
- The password never appears in a process argument, URL, log line or console output in Tauri mode; the engine runs with `CREATE_NO_WINDOW`.
- `%APPDATA%\PocketAssistant\app.json` = `{ "python": "...", "config": "...", "pythonpath": null | "..." }`; app log `%APPDATA%\PocketAssistant\logs\app.log`.
- Dashboard window: label `dashboard`, created hidden, 1280×800 centred (window-state plugin remembers it), close hides it.
- Browser development (`assistant engine --dev` + Vite + handoff file) unchanged; API port fixed (`[api] port`), Tauri origin already allowed.

## Review Focus

1. Quit while the engine is mid-job: the app waits (tray "Stopping…") and leaves no python process → Task 4 test "stop waits for a slow engine and leaves no process".
2. The app is killed or crashes: the engine stops by itself within ~5 s → Task 1 test "the engine stops when its parent is gone".
3. A terminal engine already holds engine.lock: no crash loop, a clear tray state, take-over when free → Task 4 test "a held lock is not a crash and is retried".
4. The dashboard is opened before the engine is ready, or after a restart changed the password → Task 2 tests "while the engine starts it shows Starting… and retries" and "after a 401 it asks the app again".
5. The engine prints a lot after READY: the app must keep draining its output or the engine blocks → Task 4 test "a chatty engine is not blocked by its pipe".

---

### Task 1: Engine handshake — password on stdin, READY line, parent watch, stop on end of stdin

**Files:**
- Create: `src/assistant/engine/parent.py`, `tests/test_parent.py`
- Modify: `src/assistant/engine/runtime.py` (`run_engine`), `src/assistant/cli.py` (engine options; exit code 3)
- Test: `tests/test_engine_runtime.py`, `tests/test_cli.py` (or the existing CLI test file)

**Interfaces:**
- Produces:
  - `parent_alive(pid: int) -> bool` (Windows: `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(h, 0)` via ctypes; POSIX: `os.kill(pid, 0)`). Never terminates anything.
  - `run_engine(cfg, dev=False, parent_pid: int | None = None, password_stdin: bool = False, stdin=None) -> None`; `stdin` defaults to `sys.stdin` (injectable for tests).
  - `READY_LINE = "READY port={port}"`, `PARENT_CHECK_SECONDS = 5.0` in runtime.py.
  - CLI: `assistant engine [--dev] [--parent-pid N] [--password-stdin]`; `EngineRunning` → message on stderr, exit code `ENGINE_RUNNING_EXIT = 3`.

- [ ] **Step 1: Write the failing tests:**
  - `test_parent_alive_for_this_process_and_not_for_a_finished_one` (spawn `python -c pass`, wait, assert False; `os.getpid()` → True).
  - `test_the_password_read_from_stdin_is_the_one_the_api_accepts` (a blocking stdin fake yielding `"s3cret\n"` then blocking; `/api/health` works, an authorized call with `Bearer s3cret` gets 200, any other password 401).
  - `test_ready_line_is_printed_once_the_api_listens` (`READY port=<free port>` in captured stdout, after which the port answers).
  - `test_end_of_stdin_stops_the_engine_after_the_current_job` (close the fake stdin → `run_engine` returns; a job in progress at that moment completes, nothing new starts).
  - `test_the_engine_stops_when_its_parent_is_gone` (`parent_alive` patched to flip to False; `PARENT_CHECK_SECONDS` patched small; `run_engine` returns).
  - `test_with_password_stdin_the_password_is_never_printed_or_logged` (stdout and `engine.log` lack it; no `Launch password:` line).
  - `test_an_engine_already_running_exits_with_code_3` (lock held; `main(["--config", ..., "engine"])` raises `SystemExit` with code 3, message on stderr).
  - The existing `--dev` tests keep passing unchanged.
- [ ] **Step 2: Run** `../../.venv/Scripts/python.exe -m pytest tests/test_parent.py tests/test_engine_runtime.py -q` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** With `password_stdin`, read the first line of `stdin` before starting the server and use it as `launch_password`; then a daemon thread blocks on `stdin.readline()` and sets `stop` on `''`. With `parent_pid`, a daemon thread sets `stop` when `parent_alive` turns False. Print `READY_LINE` with `flush=True` right after `server.start()`. Dev mode's print and handoff stay as they are.
- [ ] **Step 4: Run** the full suite `../../.venv/Scripts/python.exe -m pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `Engine: password on stdin, READY line, parent watch, stop on end of stdin`.

### Task 2: The dashboard inside the app

**Files:**
- Create: `app/src/lib/tauri.ts`, `app/src/lib/tauri.test.ts`
- Modify: `app/src/views/Login.tsx` (auto sign-in in the app), `app/src/views/Unreachable.tsx`, `app/src/views/Settings.tsx` (Open button), `app/src/main.tsx` (external links), view tests
- Test: `app/src/api/auth.test.tsx`, `app/src/views/Settings.test.tsx`

**Interfaces:**
- Consumes (from Task 4, through `window.__TAURI__.core.invoke`): `engine_connection` → `{ port: number, password: string }` or a rejected string `"starting"` / `"stopped"` / `"lock_held"`; `open_profile({ path })`; `plugin:opener|open_url({ url })`.
- Produces: `isTauri(): boolean` (true when `window.__TAURI__?.core?.invoke` exists), `invoke<T>(cmd: string, args?: object): Promise<T>`, `installExternalLinks(): () => void`.

- [ ] **Step 1: Write the failing tests** (a fake `window.__TAURI__ = { core: { invoke: vi.fn() } }`, removed after each test):
  - `inside the app the dashboard signs in from the app's command, without the form` (invoke resolves `{ port: 47615, password: "right" }` → dashboard shown, form never rendered).
  - `while the engine starts it shows Starting… and retries` (rejects `"starting"` twice, then resolves → "Starting the engine…" shown, then the dashboard; retry every 1 s with fake timers).
  - `after a 401 it asks the app again` (an API 401 → invoke called a second time, new password used).
  - `a port other than the dashboard's shows a clear error` (`{ port: 50000 }` → message naming both ports, no form).
  - `in the app, a link that opens a new tab goes to the default browser` (click on `<a target="_blank" href="https://example.com">` → `invoke("plugin:opener|open_url", { url: "https://example.com/" })`, default prevented).
  - `in the app, Settings can open the profile file` (an **Open** button next to the path → `invoke("open_profile", { path })`; outside the app the button is absent).
  - `in the app, the can't-reach screen says to restart from the tray` (and no `assistant engine --dev` text there).
- [ ] **Step 2: Run** `npx vitest run` (in `app/`) — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Login's auto sign-in: in the app → `invoke("engine_connection")`, retry each second while it rejects `"starting"`/`"lock_held"` (showing "Starting the engine…" or "Engine already running in a terminal"), then `signIn(password)`; outside → the existing dev handoff. `installExternalLinks` is a document click listener installed from `main.tsx` only when `isTauri()`.
- [ ] **Step 4: Run** `npm --prefix app run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Dashboard: sign in through the desktop app, open links in the browser`.

### Task 3: The Tauri shell — scaffold, settings discovery, restart policy

**Files:**
- Create: `app/src-tauri/{Cargo.toml, build.rs, tauri.conf.json, capabilities/default.json, icons/*, src/main.rs, src/lib.rs, src/settings.rs, src/backoff.rs}`
- Modify: `.gitignore` (`app/src-tauri/target/`, `app/src-tauri/gen/`, `!app/src-tauri/Cargo.lock`), `app/package.json` (devDependency `@tauri-apps/cli` 2.12.4; scripts `"tauri": "tauri"`)

**Interfaces:**
- Produces:
  - `pub struct AppSettings { pub python: PathBuf, pub config: PathBuf, pub pythonpath: Option<PathBuf> }` (serde).
  - `AppSettings::discover(exe: &Path) -> Option<AppSettings>`: walk the exe's ancestors; the nearest folder with `.venv\Scripts\python.exe` and `config.toml` gives `python`/`config`; the nearest folder with `src\assistant\__init__.py` gives the source tree; `pythonpath = Some(<source tree>\src)` only when the two folders differ (a worktree build).
  - `AppSettings::load_or_create(file: &Path, exe: &Path) -> Result<AppSettings, String>` (reads the file, or discovers and writes it; error text names the file).
  - `pub struct RestartPolicy`, `RestartPolicy::new()`, `on_crash(&mut self, at: Instant) -> Option<Duration>`: 1 s, 5 s, 30 s, 30 s…; `None` once 5 crashes fall within 10 minutes.
  - `tauri.conf.json`: productName "Pocket Assistant", identifier "dev.pocketassistant.app", `withGlobalTauri: true`, `build.frontendDist: "../dist"`, `devUrl: "http://localhost:5173"`, `beforeBuildCommand: "npm run build"`, one window `dashboard` (hidden, 1280×800, centre), `bundle.active: false`.

- [ ] **Step 1: Write the failing Rust unit tests:** `discover_finds_the_repo_from_a_release_build` (temp tree `repo/.venv/Scripts/python.exe`, `repo/config.toml`, `repo/src/assistant/__init__.py`, exe at `repo/app/src-tauri/target/release/x.exe` → python/config under repo, `pythonpath` None); `discover_sets_pythonpath_for_a_worktree_build` (exe under `repo/.worktrees/w/app/...`, source tree `repo/.worktrees/w` → `pythonpath = repo/.worktrees/w/src`); `discover_returns_none_without_a_venv`; `load_or_create_writes_the_file_once`; `restart_delays_are_1_5_30_then_30`; `restart_gives_up_after_5_crashes_in_10_minutes`; `old_crashes_do_not_count` (crashes 11 minutes apart never give up).
- [ ] **Step 2: Run** `cargo test` (in `app/src-tauri`) — Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** the modules; `main.rs` builds an app that opens nothing yet but the hidden dashboard window. Icons: a placeholder amber circle generated with `npx tauri icon` from a committed `icons/source.png`.
- [ ] **Step 4: Run** `cargo test` and `npx --prefix app tauri build --no-bundle` — Expected: tests pass; a release exe is built.
- [ ] **Step 5: Commit** `Desktop app: Tauri shell, settings discovery and restart policy`.

### Task 4: Supervising the engine, tray, single instance, autostart

**Files:**
- Create: `app/src-tauri/src/engine.rs`, `app/src-tauri/tests/supervisor.rs`, `app/src-tauri/tests/fake_engine.py`
- Modify: `app/src-tauri/src/lib.rs` (tray, plugins, commands), `app/src-tauri/capabilities/default.json`

**Interfaces:**
- Consumes: `AppSettings`, `RestartPolicy` (Task 3); the engine's CLI (Task 1).
- Produces:
  - `parse_ready_line(line: &str) -> Option<u16>`.
  - `pub enum EngineState { Starting, Running { port: u16 }, Stopping, Stopped, GaveUp, LockHeld, Missing(String) }`.
  - `pub struct Supervisor` with `start(&self)`, `stop(&self, wait: Duration)`, `restart(&self)`, `state() -> EngineState`, `connection() -> Result<(u16, String), &'static str>` (`"starting"`, `"stopped"`, `"lock_held"`).
  - Commands: `engine_connection() -> Result<Connection { port: u16, password: String }, String>`; `open_profile(path: String) -> Result<(), String>` (only an existing file named `profile.toml`; opened with the opener plugin's Rust API).
  - Tray: left-click → show + focus `dashboard`; menu *Open dashboard*, *Restart engine*, *Start with Windows* (check, autostart plugin; enabled on first run), *Quit* (stop with 150 s wait, then exit); tooltip "Pocket Assistant · engine running / starting / stopping / stopped / already running in a terminal".
  - Single instance: a second launch shows the dashboard. Dashboard close → hide.

- [ ] **Step 1: Write the failing tests** (unit tests in `engine.rs`; integration tests in `tests/supervisor.rs` using `fake_engine.py` run by the `python` the tests find on PATH, behaviour chosen by an argument):
  - `parse_ready_line` accepts `READY port=47615` and rejects anything else.
  - `the password reaches the engine on stdin and READY makes it running` (fake echoes a hash of the line it read to a temp file; state becomes `Running { port }`).
  - `stop waits for a slow engine and leaves no process` (fake exits 3 s after end of stdin; `stop(150 s)` returns after ≈3 s; the pid is gone).
  - `a crash is restarted per the policy` (fake exits 1 immediately; the supervisor relaunches after ≈1 s; with the policy exhausted the state is `GaveUp`).
  - `a held lock is not a crash and is retried` (fake exits 3 twice, then runs; state `LockHeld` then `Running`; the policy recorded no crash).
  - `a chatty engine is not blocked by its pipe` (fake prints 20,000 lines after READY and then waits for end of stdin; it is still alive and stops on `stop`).
  - `a missing python gives Missing with the settings file named`.
- [ ] **Step 2: Run** `cargo test` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Spawn `python -m assistant.cli --config <config> engine --parent-pid <own pid> --password-stdin` with `CREATE_NO_WINDOW`, piped stdin/stdout/stderr, `PYTHONPATH` from settings; one reader thread per pipe forwards every line to `app.log` (never the password) and watches stdout for READY; a waiter thread handles exit codes (0 after a requested stop → `Stopped`; 3 → `LockHeld` and a 10 s retry; anything else → `RestartPolicy`).
- [ ] **Step 4: Run** `cargo test` and `npm --prefix app run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Desktop app: supervise the engine; tray, single instance, autostart`.

### Task 5: Live check on the PC and docs

**Files:**
- Modify: `README.md` (a "Desktop app" section: build, `app.json`, tray, quitting), `CLAUDE.md` (architecture line for `app/src-tauri/` if it lists units)

- [ ] **Step 1:** Release build from the worktree (`npx --prefix app tauri build --no-bundle`); run it once so it writes `app.json` (expect `pythonpath` = the worktree's `src`).
- [ ] **Step 2:** Stop the terminal engine (one Ctrl+C in "pocket engine main") and the Vite tab; start the app. Run the spec's manual checklist and record each result in the ledger: tray icon and menu; the dashboard opens fast, signs in, live events and actions work; a second launch shows the dashboard; closing hides it; *Restart engine*; killing the engine process → restarted; *Quit* → tray "Stopping…", then no python process; starting `assistant engine` in a terminal while the app runs → refused with code 3 by the terminal; the reverse (terminal first, then the app) → tray "already running in a terminal", app takes over after the terminal engine stops; the autostart registry entry points at the release exe; no console window at any point.
- [ ] **Step 3:** Fix what fails (test first for behaviour); ledger each.
- [ ] **Step 4:** Docs; `pytest -q`, `cargo test`, `npm --prefix app run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Docs: the desktop app`.
