# M4 part 1: the desktop app on the main computer

Builds on `2026-10-25-desktop-app-redesign-design.md` (sections 3, 9, 10 and 11), whose M4 this is the first half
of, and on the M3 dashboard. Part 2 (the laptop as a viewer) gets its own design later.

## Why

The owner's "faster and smoother" starts with **no terminal for daily use**: today the engine and the dashboard each
need a terminal tab, and pressing Ctrl+C in the wrong one stops the engine. M4 makes Pocket Assistant a desktop app
on the PC that starts with Windows, keeps the engine running by itself, and opens the dashboard instantly from a tray
icon.

## What is settled

- **The transparency spike passed** on this PC (2026-11-18, throwaway, Tauri 2.12): a transparent, borderless,
  always-on-top 180×180 window rendered correctly and could be dragged; a hidden dashboard window was created at start
  and appeared **6 ms** after a click (target 300 ms); the app used about 38 MB. The see-through corners still catch
  clicks (a note for the chibi, M5). The owner saw nothing off.
- **Self-built (unsigned) apps must be allowed to run** until M7 signs them. Building needs Rust (MSVC) and the
  Visual Studio C++ Build Tools.
- **The engine runs from the project's own Python** (the owner picked approach A, below). Bundling the engine
  (PyInstaller), the installer, and moving config and data to `%APPDATA%` / `%LOCALAPPDATA%` move to **M7**, where
  a clean install on another machine needs them.
- **M4 comes in two parts:** this one (the PC) first; the laptop viewer (main/viewer mode, pairing, connection banner,
  offline snapshot) second. How the app runs on the laptop is decided then.

## Goals

1. **A Tauri 2 app** in `app/src-tauri/` (Rust) whose dashboard window is the existing M3 React dashboard (`app/`,
   built by Vite).
2. **Tray icon.** Left-click opens the dashboard. Menu: *Open dashboard*, *Restart engine*, *Start with Windows*
   (a check item), *Quit*. The tooltip says the engine's state (running, starting, stopped).
3. **Starts with Windows** (Tauri's autostart plugin, on by default, switchable from the tray), straight to the tray
   with the dashboard hidden. **One copy only** (single-instance plugin): starting it again shows the dashboard.
4. **The app runs the engine.** It starts the engine with the project's Python and no console window, does the
   spec's handshake, hands the dashboard what it needs to connect, restarts the engine if it crashes, and stops it on
   *Quit*.
5. **The dashboard signs in by itself** inside the app, through a Tauri command, with no password form. The browser
   setup for development (`assistant engine --dev` + Vite + the password handoff file) keeps working unchanged.
6. **Instant dashboard.** The dashboard window is created hidden at start and shown on demand; closing it hides it
   again, so it opens in well under 300 ms every time.

## Non-goals (this part)

- The chibi window (M5), main/viewer mode and the laptop (part 2), Telegram (M6).
- A bundled engine, an installer, code signing, the data-folder move (M7).
- Auto-update.

## Anti-goals (a failure even if it works)

- **An engine left running after *Quit*, or two engines.** Quitting must leave no Python process behind; a crash of
  the app must take the engine down within seconds (the parent watch).
- **Losing work on quit.** *Quit* stops the engine the way one Ctrl+C does today: the current job finishes (no paid
  reply is thrown away), then it exits. (Windows shutdown is the one exception, below.)
- **A console window flashing up** at login or on restart.
- **The launch password leaking:** never in a process argument, a URL, a log line or browser storage outside the app.

## Constraints

- Every pinned version at least 14 days old: `tauri` 2.12.5, `tauri-build` 2.7.4, `@tauri-apps/cli` 2.12.4, and the
  plugins (`tauri-plugin-autostart`, `tauri-plugin-single-instance`) at their newest releases from on or before
  2026-11-04. Exact pins and a committed `Cargo.lock`.
- The API keeps its fixed port (`[api] port`, default 47615) so `tailscale serve` keeps working; the API already
  allows Tauri's origin (`http://tauri.localhost`).
- All `CLAUDE.md` hard rules stay (read-only mail, no secrets in logs, paid calls need approval).
- `engine.lock` still allows one engine: if one is already running (a terminal engine), the app says so in the tray
  and does not start a second.

## Approaches (owner picked A)

- **A. The project's own Python (chosen).** The app starts `<repo>\.venv\Scripts\python.exe -m assistant.cli
  --config <repo>\config.toml engine ...` with no console window. Works today; nothing new can be flagged by
  antivirus; bundling waits for M7.
- **B. A PyInstaller-bundled engine now,** with the data move. Closer to the final product, but a 30–50 MB bundle,
  possible antivirus false alarms and more work before daily use. Deferred to M7.

## How it works (decided)

**Engine start-up (the spec's handshake, section 3):**
- The app starts the engine with `--parent-pid <app pid>` and **writes a random 32-byte password to the engine's
  stdin** (one line). The engine reads it instead of making its own, binds `127.0.0.1:<[api] port>`, and prints
  `READY port=<n>` on stdout. The app waits up to 30 s for that line.
- New engine options for this: `--parent-pid <pid>` and `--password-stdin`. `--dev` and the plain terminal start keep
  working as now.
- **Parent watch:** with `--parent-pid`, the engine checks every 5 s that the process is alive and stops (like one
  Ctrl+C) when it is gone.
- **Stopping (owner's choice: wait for the job):** on *Quit* (and on *Restart engine*) the app closes the engine's
  stdin; with `--password-stdin` the engine treats the end of stdin as a stop request (one Ctrl+C: nothing new starts,
  the current job finishes). The tray shows "Stopping…" and the app exits when the engine has. A model call can take
  up to 120 s and a mail step up to 30 s, so the app waits up to **150 s** before it kills the engine; usually it is
  a few seconds.
- **Windows shutdown, restart or sign-out is a hard stop.** Windows gives apps only seconds; the engine is ended, and
  any email whose call was cut off is given back and retried at the next start (`Store.release_orphaned_claims`), so
  at worst one call (about $0.0005) is paid twice. No shutdown blocking.
- **Crashes:** if the engine exits on its own, the app restarts it after 1, 5, then 30 s, and gives up after 5 crashes
  within 10 minutes. Given up, the tray reads "Engine stopped" with *Restart engine*, and the dashboard shows its
  "can't reach the engine" screen with words that fit the app ("Restart it from the tray icon") instead of the
  terminal command.

**The dashboard in the app:**
- The window loads the Vite build of `app/`. On start, the dashboard asks the app (a Tauri command) for the engine's
  password and port and signs in with them; before the engine is ready the command waits (or the dashboard shows
  "Starting…" and retries). The password lives in memory and the dashboard's session storage, as in M3.
- The browser path stays: outside Tauri the dashboard uses the dev handoff or the form, exactly as now.

**Where the app finds the engine:** a small settings file, `%APPDATA%\PocketAssistant\app.json`, holding the Python
path and the config path. On first run without it, the app fills it from its own location in the repository (the
release build sits inside `app\src-tauri\target\`) and saves it; if those files don't exist, the tray says
"Can't find the engine" and names the file to edit.
- **For development only,** `app.json` may also name a source folder the engine is run from (passed as `PYTHONPATH`).
  The project's `.venv` is an editable install of the main checkout, so without it an app built in a worktree would
  run the main checkout's engine, which lacks the new start-up options until they are merged. (Final review: a
  worktree build works this folder out from its own place at every start instead of saving it, so a saved folder
  can't outlive its worktree or redirect the main checkout's build; `app.json` can still name one to override.)

**One engine at a time:** if `engine.lock` is held (a terminal engine is running), the app shows "Engine already
running in a terminal" in the tray, does not start a second one, and takes over by itself once the lock is free.

## Testing

- **Engine (pytest):** the password read from stdin is the one the API accepts and is never printed or logged; the
  `READY port=<n>` line; the parent watch stops the engine when the parent is gone; the end of stdin stops it after
  the current job; the old starts (`--dev`, plain) unchanged.
- **App (Rust unit tests):** the restart backoff policy (1, 5, 30 s; give up after 5 crashes in 10 minutes); reading
  the `READY` line; `app.json` defaults.
- **Dashboard (Vitest):** inside Tauri (a faked `window.__TAURI__`) it signs in from the app's command without the
  form; outside, nothing changes; the "can't reach" screen's app wording.
- **Manual checklist** (the spec's section 10), run on this PC: starts at login to the tray with no console window;
  tray left-click and menu; a second start shows the dashboard; dashboard opens fast every time; *Quit* leaves no
  python process; killing the engine process makes the app restart it; a terminal engine already running is reported,
  not duplicated; the dashboard works live (actions, events) inside the app.

## Left to the builder (my calls, so the owner can check them)

- The tray and app icon: a placeholder amber circle until the chibi art exists.
- The dashboard window's size and position: remembered between runs (Tauri's window-state plugin) if it costs little,
  otherwise a fixed 1280×800 centred.
- Exact tray tooltip and menu wording.
- The release build is what starts with Windows; running it from `app\src-tauri\target\release\` is fine until M7's
  installer. `tauri dev` (development) runs against `assistant engine --dev`, which already allows the Vite origin.
- `Cargo.lock` is committed: `.gitignore` ignores `*.lock`, so it gets an exception (`!app/src-tauri/Cargo.lock`).
- One launch password per app run, a fresh one after each engine restart; the dashboard asks the app again after a 401.
- Why a start failed (the engine's error output) goes to the app's own log and a short tray message.
- Links to Gmail and to opportunities open in the default browser (Tauri's opener plugin); the Settings page's
  profile path gets an *Open* button there too, since M3 offered only *Copy* "until M4".
