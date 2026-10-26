# Pocket Assistant: desktop app redesign

**Status:** design approved in conversation on 2026-10-25, section by section. This spec is waiting for the owner's review.
**Replaces:** Steps 9–13 of `docs/phase1-plan.md`. Steps 1–8, the code on the `phase-1` branch, carry over unchanged as the engine's internals.

---

## 1. Why this redesign

Today the assistant is a set of terminal commands, plus a scheduled task that starts cold every 30 minutes. The owner wants it to feel fast and smooth, and wants a polished dashboard behind the chibi.

### What the owner said

- **Keep the mail setup.** Every account forwards into the personal Gmail, and the assistant reads only that.
- **"Faster and smoother" means three things:**
  - no terminal for daily use, things open instantly, and new mail appears within minutes;
  - processing is quicker and cheaper;
  - a usable app exists sooner.
- **Laptop-only is fine.** It works while the laptop is on and catches up when it's opened. No cloud server.
- **Dashboard, first version:**
  - ranked opportunities;
  - today's report;
  - important emails;
  - actions on items: star, dismiss and applied.
- **Mobbin references.** The dashboard is designed from proven real-world patterns found on Mobbin (section 6).
- **Telegram stays.** A morning digest and urgent alerts, sent while the laptop is on.
- **The digest is a clean template.** No model writes it.
- **The chibi** has animations and pre-written speech bubbles, and a click opens the dashboard. No AI chat.

### Assumptions the owner didn't correct

- The app starts with Windows. The chibi floats on the desktop, the dashboard is a normal window, and a tray icon covers quit and settings.
- "Within minutes" means checking Gmail about every 2 minutes while the laptop is on.
- AI is used only to read emails, with Haiku 5.5. The monthly cost is roughly $0.10–1.
- **"Important emails"** are emails that:
  - produced opportunities;
  - are from the university;
  - need action, a new field extraction returns;
  - or were flagged for addressing the AI.
- Nothing already built is thrown away. All hard rules in `CLAUDE.md` stay.
- The dashboard uses React and TypeScript; the owner knows them from FlashDeck.
- The chibi uses placeholder art until the owner's character art exists.

### Success criteria

| Measure | Target |
|---|---|
| New email in Gmail → ranked in the dashboard | ≤ 2.5 minutes while online |
| Click the chibi → dashboard visible | ≤ 300 ms (the window is created hidden at start-up) |
| Dashboard list and search responses | ≤ 50 ms at 5,000 opportunities |
| Engine when idle | about 0% CPU, under 150 MB of memory |
| Terminal needed day to day | never |
| LLM cost | under $1 a month in normal use; the caps from Step 5 still apply |

---

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| A1 | **One long-running engine** replaces the 30-minute scheduled task | A warm process answers instantly, polls cheaply and pushes live updates. A cold start every 30 minutes can do none of these. |
| A2 | **Tauri 2 shell with a Python engine as a sidecar.** Rejected: all-Python pywebview, and Electron | Tauri keeps the transparent, always-on-top chibi window solid while the app stays light (about 10 MB, low memory). pywebview's transparency is unreliable on Windows. Electron is about 150 MB and heavy for an always-on app. |
| A3 | **Gmail change feed** (`users.history.list`) every 2 minutes, with a fallback to the existing search sync | When nothing changed, it costs one tiny call. The docs say a 404 means "do a full sync", and a bookmark typically lasts at least a week. |
| A4 | **Only the engine touches SQLite.** The windows use a local API | One writer means no lock fights. The windows can't corrupt data. |
| A5 | **Template digest.** No Opus, and no chibi chat | Instant, free and predictable. The character's personality lives in pre-written lines. |
| A6 | **Laptop-only** | Free and private. Mail arriving while the laptop is off is caught up on wake. |
| A7 | **Lighter review process** | A full two-reviewer check runs only on code where mistakes cost money or privacy. Screens get one review per milestone. |

---

## 3. Architecture

```
You log in to Windows
  └─ Pocket Assistant app (Tauri 2, tray icon, single instance)
       ├─ Chibi window: transparent, always on top, sprites and speech bubbles
       ├─ Dashboard window: React + TypeScript, created hidden, shown on chibi click
       └─ launches → Engine (Python, bundled sidecar)
                       ├─ Scheduler: Gmail every 2 min, digest at 08:00, privacy sweep hourly
                       ├─ Brain: filter → Haiku extract → dedupe/score (Steps 3–8, reused)
                       ├─ SQLite (engine only)
                       ├─ Telegram: morning digest and urgent alerts (send only)
                       └─ Local API on 127.0.0.1, with a live event stream (SSE)
```

### The units

| Unit | Does | Is used through | Depends on |
|---|---|---|---|
| **Engine** (`src/assistant/`, Python) | All data and all work | The local API. The `assistant` CLI also stays for debugging. | Gmail API, Anthropic API, Telegram API, SQLite |
| **Scheduler** (`engine/scheduler.py`) | Runs each job when it is due, against the wall clock | Jobs register a name, how often they run, and a function | A clock (injected; a fake clock in tests) |
| **Jobs** (`engine/jobs.py`) | `check_mail`, `extract`, `digest`, `alerts`, `sweep` | The scheduler | Existing modules from Steps 1–8 |
| **Local API** (`engine/api.py`, FastAPI) | Reads, actions and the event stream | HTTP with a per-launch password | Store, rank, events |
| **Events** (`engine/events.py`) | Fans out events from jobs to every open stream | `publish(kind, payload)` / `subscribe()` | — |
| **Telegram** (`telegram.py`) | Pairing, sending the digest and alerts | Called by the `digest` and `alerts` jobs | Bot token from keyring, httpx |
| **Tauri shell** (`app/src-tauri/`, Rust) | Windows, tray, autostart, single instance, launching and stopping the engine | — | Tauri plugins: shell (sidecar), autostart, single-instance |
| **Front end** (`app/src/`, React + TypeScript + Vite) | Two pages: `chibi.html` and `dashboard.html` | — | Local API, through types generated from its OpenAPI description |

### Engine process model

- The engine is one Python process. Uvicorn serves the API on the main asyncio loop.
- The scheduler runs in one background thread. It wakes every 15 seconds and runs whatever is due.
- **Extraction runs in a small thread pool, 4 workers.** Each worker has its own SQLite connection.
  - Step 5's ledger reservation (`BEGIN IMMEDIATE`) keeps the cap checks correct across workers.
  - Step 8's claim and its guarded save keep each email single-processed.
- Jobs publish events through a thread-safe queue handoff (`loop.call_soon_threadsafe`).
- **Start-up handshake.** Tauri starts the engine with `--parent-pid <pid>` and writes a random 32-byte password to the engine's stdin. Stdin rather than an argument, because other programs can read process arguments. The engine binds a free port on 127.0.0.1 and prints `READY port=<n>` to stdout. Tauri then hands the port and password to its two windows through a Tauri command.
- **The engine exits when its parent is gone.** It checks every 5 seconds. Tauri's sidecar docs don't promise to stop the child when the app exits, so the engine looks after itself.
- **Single instance.**
  - Tauri's single-instance plugin focuses the running app.
  - The engine also holds `engine.lock`, which contains its PID, in the data folder.
  - While the engine runs, `assistant` commands that write data refuse with "the app is running; use the dashboard". Read-only commands still work.
- **Developer mode.** `assistant engine --dev` prints the port and password and also allows the Vite dev server's origin. This lets the dashboard run in a browser in M3, before the Tauri shell exists.

---

## 4. Mail flow and timing

### On start, and on wake from sleep

Wake is detected when the wall clock jumps more than 3 minutes between scheduler ticks.

1. **Sweep:** delete bodies older than 7 days (the existing privacy rule).
2. **Check mail:** fetch everything since the stored history bookmark.
3. **Extract:** read the queue with 4 workers. About 80 emails take roughly 1 minute.

### Jobs and timings

| Job | When | What |
|---|---|---|
| `check_mail` | Every 2 min while online; back off 5 → 15 min while offline | Calls `history.list` from the stored bookmark (message additions only), then fetches and parses the new messages through the existing code. A 404 or a missing bookmark falls back to `sync_mail(days=min(days since last good check, 7))`. Stores the new bookmark. |
| `extract` | Right after `check_mail` adds mail, and once a minute while the queue isn't empty | The Step 8 pipeline, run with 4 workers. At most 60 calls a run, as now. |
| `digest` | At 08:00 HCMC time, or at the first wake after it if the laptop was off. Once a day. | Builds the template digest, stores it in `digests`, and sends it to Telegram once. |
| `alerts` | After each `extract` | Sends an urgent alert when a **newly seen** item scores ≥ 80 and its deadline is within 3 days of today's HCMC date, or when a university email needs action. "Newly seen" means inserted in this run, not merged into an existing item. At most 3 a day, never twice for the same item. |
| `sweep` | At start-up, then hourly | The existing body purge |

### Events on the live stream

| Event | When | What it drives |
|---|---|---|
| `items_new` | After a save adds items | The chibi turns `excited` and says a line. The dashboard refreshes. |
| `digest_ready` | When the digest is built | The chibi shows `letter` until the dashboard is opened |
| `status` | Online or offline, the last check, an error, a cap, Gmail auth lost | The dashboard banner. The chibi turns `worried` or `sleepy`. |
| `alarm` | A starred or applied item's deadline is within 24 hours | The chibi shows `alarm`, once per item |

---

## 5. Local API

All routes sit under `/api` and return JSON, except the event stream.

| Route | Purpose |
|---|---|
| `GET /api/health` | Engine version and status. Needs no password, but returns nothing private. |
| `GET /api/today` | Counts (new today, deadlines this week, starred, applied), top 5 picks, the needs-action list, the next 7 days of deadlines, spend against the caps, and status (online, last check, last error) |
| `GET /api/opportunities` | Query: `tab=for_you\|all\|starred\|applied\|dismissed`, `kind`, `deadline_within`, `min_score`, `q`. Returns rows with the fields needed for the table. |
| `GET /api/opportunities/{id}` | Full detail: the score breakdown (`rank.explain`), and the source emails (sightings) with their Gmail links |
| `POST /api/opportunities/{id}/{action}` | `action` is `star`, `unstar`, `dismiss`, `restore` or `applied`. Each action is also written to `item_actions`. |
| `GET /api/emails` | Query: `section=important\|other`, `days`. Rows show sender (display name), subject, excerpt and badges. |
| `GET /api/emails/{message_id}` | Excerpt, badges, the opportunities found in it, and its Gmail link |
| `GET /api/reports/spend?days=30` | Daily spend, the month total against the cap, and tokens. From `llm_calls`. |
| `GET /api/reports/digests` / `GET /api/reports/items?weeks=8` | Past digests; items per kind and per source each week |
| `GET /api/search?q=` | The Ctrl+K palette: opportunities, emails and pages, each with its type |
| `GET /api/settings` / `POST /api/settings/rescore` / `POST /api/settings/telegram/pair` | Connection status (Gmail, Telegram, Anthropic), caps, the profile summary, rescore, Telegram pairing |
| `GET /api/events` | Server-sent events (section 4). Read with `fetch` streaming, so the password stays in a header and never in a URL. |

**Definitions:**
- **"For you"** means open items (active, not dismissed, deadline not passed) with score ≥ `for_you_min_score`. That is a new `[dashboard]` setting in `config.toml`, default 60.
- **"Open in Gmail"** links to `https://mail.google.com/mail/?authuser=<gmail address>#all/<message_id>`. The address is stored at sync, so the right account opens even when several are signed in.

**Security, enforced on every route:**
- The server binds 127.0.0.1 only.
- Every request needs `Authorization: Bearer <launch password>`, except `/api/health`.
- The `Host` header must be `127.0.0.1:<port>`. This blocks DNS-rebinding attacks.
- CORS allows only the Tauri origins, plus the Vite dev origin in `--dev` mode.

**Contract:** `openapi-typescript` generates the front end's types from FastAPI's OpenAPI document. A test fails when the committed types fall out of date.

---

## 6. Dashboard and chibi

The patterns come from Mobbin references. We borrow the pattern, never the look.

### Shared patterns

- **Shell.** A quiet left sidebar with counts, neutral surfaces, and colour only where it means something: score level, kind, deadline urgency. It follows Windows' light or dark mode. Reference: the [Linear issue list](https://mobbin.com/screens/212fda35-366e-4dc0-a1d1-3b679659d6ab).
- **Details slide in from the right.** Clicking a row opens a right-hand side panel; Esc closes it. References: [Twenty](https://mobbin.com/screens/ddc70bb6-5a08-444c-8ef8-a12a0050a428), [Mixpanel](https://mobbin.com/screens/3099958d-8328-4c5f-aff0-4d190e981c3b).
- **Ctrl+K command palette.** It finds opportunities, emails and pages; each result shows its type, and the bottom row lists the shortcuts. References: [Bonsai](https://mobbin.com/screens/71409a31-a14b-4202-8cf3-6534833cd4a1), [StackAI](https://mobbin.com/screens/bbcc94bb-f535-4dca-8532-56b135fce5c3).
- **Keyboard:** `j`/`k` move, `Enter` opens, `Esc` closes, `s` star, `d` dismiss, `a` applied, `/` search, `g t` / `g o` / `g e` / `g r` go to a view.
- **Empty states** show the chibi with one line instead of a blank table. Reference: [Peerlist](https://mobbin.com/screens/cac39269-4d26-4a8c-b51f-f4fd3e9b93a7).
- **Stack:** React 18, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Table and TanStack Query, plus a small chart library for Reports. Exact versions are pinned in the plan, after checking current docs.

### Views

1. **Today**
   - **Header.** A greeting with the chibi portrait, and four number cards: New today, Deadlines this week, Starred, Applied. Each card opens its filtered list. Reference: [hilos](https://mobbin.com/screens/0ddf5aec-429d-4ad1-9e36-be02ff29a6e1).
   - **"For you today":** what needs action, with counts and one button each. Reference: [Deel](https://mobbin.com/screens/403abcf1-892f-4036-9cbd-ba951c6dce7e).
   - **"Upcoming deadlines":** the next 7 days. Reference: [Bonsai](https://mobbin.com/screens/34d0f00d-1ca1-4c73-b388-14b96f0b88a9).
   - **Status line** at the bottom: last checked, online or offline, and spend this month.
2. **Opportunities**
   - **Tabs with counts:** For you · All · Starred · Applied · Dismissed. Reference: [Contra Jobs](https://mobbin.com/screens/0261bdc3-e4cc-4e5f-baf2-28dabece24a8).
   - **Rows** show score, kind chip, title, organizer, place and deadline ("in 3 days"), plus "seen ×N". Each row has a one-line "why" note and inline Dismiss and Applied buttons.
   - **Filter chips:** kind, deadline window, minimum score. Reference: [Sentry](https://mobbin.com/screens/b12617bc-63e9-40d4-9518-536ab23197c2). A "N hidden by filters · Clear" line shows when filters hide items.
   - **Side panel:** the score breakdown, eligibility and prize, plus **Open link** and **Open email in Gmail** for each sighting.
3. **Important emails**
   - **List:** dense rows of sender, subject and a grey excerpt, grouped by day, with category chips. Sections: **Important** and **Other**. References: [Notion Mail](https://mobbin.com/screens/6cbf9d6c-b0d2-4d89-9ee5-57bbd6fec0cf), [Superhuman](https://mobbin.com/screens/794d4dae-d263-4883-a6e1-bdcce480ac8c).
   - **Reading pane:** the badges, Gmail's excerpt, the action note, the opportunities found in the email, and **Open in Gmail**. The full body is never stored.
4. **Reports**
   - Total spend with daily bars, a budget bar such as "October $0.03 / $5", and a small token chart. Reference: [OpenAI Platform usage](https://mobbin.com/screens/008725f3-1e67-4ff3-96c4-d56f14d23e15).
   - Past digests, and items per kind and source each week, with a breakdown table. Reference: [Vercel](https://mobbin.com/screens/fd0e54a5-45cc-4a47-be0d-3fdb7e804ec7).
5. **Settings**
   - **Connected accounts:** Gmail, Telegram and Anthropic, each with status and a Connect or Reconnect button. Reference: [Linear](https://mobbin.com/screens/3b7417c7-551f-4935-890f-4788e4d8d334).
   - Caps and `for_you_min_score`.
   - A read-only profile summary, with **Open profile.toml** and **Rescore**.

### The chibi

- **Window.** About 180×180 px, transparent, borderless, always on top, draggable, with its position remembered.
  - **Left-click** opens the dashboard, or the view a speech bubble refers to.
  - **Right-click** opens a menu: Open dashboard · Snooze 1 h · Hide · Quit.
- **Moods:**

  | Mood | When |
  |---|---|
  | `idle` | Default |
  | `sleepy` | Offline, 23:00–07:00, or snoozed |
  | `excited` | `items_new` with an item scoring ≥ 60. Lasts 8 s. |
  | `letter` | `digest_ready`, until the dashboard is opened |
  | `worried` | A job error, a cap reached, or Gmail auth lost. Lasts until resolved. |
  | `alarm` | `alarm` events |

- **Speech bubbles.**
  - Lines live in `character/lines.toml`: event → list of lines with `{placeholders}`, one picked at random.
  - At most 3 unprompted bubbles a day, as `CLAUDE.md` says.
  - A bubble disappears after 10 s; clicking it opens the related view.
- **Art.** `character/sprites/<mood>.png` (a sprite sheet) plus `<mood>.json`, holding frame size, frame count, fps and whether it loops. Placeholder sheets ship first. Swapping in real art means dropping files in place.

---

## 7. Telegram

- **Bot token.** Stored in Windows Credential Manager with `assistant set-key telegram`. The vault gains a `telegram` kind with a validated token shape and redaction.
- **Pairing.**
  1. Settings shows a 6-digit code.
  2. The engine long-polls `getUpdates` for up to 10 minutes, waiting for `/start <code>`.
  3. When it arrives, the chat id is stored in `state`.
  4. Polling stops. After pairing, the bot reads nothing.
- **Sending.** Only to the stored chat id, through `sendMessage` with HTML parse mode and link previews off. httpx is used directly; no bot framework is needed for send-only.
- **Rendering (required by Step 8).** Every model-written field is escaped and has its link-like text defanged, for example `example[.]com`, so Telegram can't turn it into a link. Only an item's `url` becomes a link.
- **Digest:**
  - new For-you items since the last digest (top 10);
  - deadlines within 7 days;
  - emails needing action;
  - one line on spend.

  Messages are split at Telegram's 4,096-character limit.

---

## 8. Data model

All changes are new numbered migrations in the existing style; nothing is rebuilt.

**v6: actions and sightings**
- `opportunities.starred INTEGER NOT NULL DEFAULT 0`
- A new `sightings` table: `opportunity_id`, `message_id`, `seen_at`, with primary key `(opportunity_id, message_id)`. It's written inside `save_extraction`.
- A new `item_actions` table: `id`, `opportunity_id`, `action` (CHECK: star, unstar, dismiss, restore, applied), `at`.

**v7: needs-action, digests and state**
- `mail_items.needs_action INTEGER NOT NULL DEFAULT 0`
- `mail_items.action_note TEXT`, holding at most 120 characters, written by the model and sanitized.
- A new `digests` table: `digest_date` (primary key), `text`, `html`, `item_ids` (JSON), `created_at`, `sent_at`, `send_error`.
- A new `state` table: `key` (primary key), `value`. It holds:
  - `gmail_history_id`, `gmail_address`, `last_ok_check_at`, `last_check_error`;
  - `telegram_chat_id`;
  - `alerts_sent:<date>`, `alerted_items`.

**Extraction change.** `Extraction` gains two required fields, and the prompt gains two lines saying what they mean:
- `needs_action: bool`: the email asks the candidate to do something by a date, such as register, pay, submit or reply;
- `action_note: str | null`.

The synthetic fixtures are updated. The recorded smoke fixture is re-recorded once, after the change. That is about $0.0006, and the owner approves it.

**Data location, from M4.** Config, profile and `credentials.json` move to `%APPDATA%\PocketAssistant\`; the database goes to `%LOCALAPPDATA%\PocketAssistant\`. On first launch the app offers to move the existing files out of the repository folder. The CLI's `--config` option still works.

---

## 9. Errors

| What happens | What the engine does | What you see |
|---|---|---|
| No internet | Backs off 2 → 5 → 15 min. Nothing is lost. | Chibi `sleepy`; the banner reads "Offline since 10:42" |
| Gmail refresh token revoked or expired | The `check_mail` job stops; the others continue | Chibi `worried`; banner and Settings show **Reconnect Gmail** |
| Anthropic 4xx, a cap, or busy | Step 8's rules: emails stay queued, and the reason is stored | The banner gives the reason (the Step 6 hint text) |
| A job raises an unexpected error | Logged (type and message, redacted) and published as `status`; the next run tries again | Banner; chibi `worried` |
| The engine crashes | Tauri restarts it after 1, 5, then 30 s, and stops after 5 crashes within 10 minutes | Chibi `worried`. After it gives up: "Engine stopped, see the log", with **Restart** |
| A second app or engine is started | The single-instance plugin focuses the first app; `engine.lock` refuses a second engine | Nothing changes |
| A Telegram send fails | Recorded in `digests.send_error` and retried at the next job run, never more than once a day per digest | Settings shows the last error |

**Logs.** `%LOCALAPPDATA%\PocketAssistant\logs\engine.log`, rotated at 5 files of 1 MB. Logs hold no mail bodies and no keys: they record class names, ids and `vault.redact` output only.

---

## 10. Testing

- **Engine (pytest, offline):**
  - The existing 478 tests stay.
  - New: the scheduler with a fake clock (due times, wake detection, digest catch-up).
  - The history feed against a fake Gmail service: normal case, pagination, 404 fallback, missing bookmark.
  - The 4-worker extraction: no double processing, caps respected.
  - API tests with FastAPI's TestClient: password, host check, CORS, every route.
  - Events.
  - Telegram rendering: escaping, defanging, splitting.
  - Single-instance lock.
- **Contract:** a test that the generated TypeScript types match the current OpenAPI document.
- **Front end (Vitest + React Testing Library):** each view against mocked API responses, keyboard shortcuts, the side panel, the palette, empty states.
- **Tauri:** a short manual checklist, run each time M4 or M5 changes:
  - transparency and always-on-top on this PC;
  - tray menu;
  - autostart after reboot;
  - the engine exits when the app quits or is killed;
  - single instance.
- **Mutation checks** continue for safety code: API auth, the history fallback, alerts limits.

---

## 11. Build order

Each milestone is small steps, with tests after each and a commit when it works.

| # | Milestone | Done when |
|---|---|---|
| **M1** | **Engine core:** scheduler, jobs, history feed with fallback, 4-worker extraction, `needs_action`, migrations v6–v7, `engine.lock`, `assistant engine` | Run in a terminal, mail is read and ranked within about 2 minutes, unattended. A fresh 7-day sync replaces the expired backlog. |
| **M2** | **Local API and events:** password, host and CORS checks, all routes, SSE, generated TypeScript types | The API tests pass, and `curl` with the password returns real data |
| **M3** | **Dashboard**, in the browser against `assistant engine --dev`: Today → Opportunities (side panel, actions) → Emails → Reports → Settings → Ctrl+K | Daily use works in the browser |
| **M4** | **Tauri app.** It **starts with a short spike** confirming a transparent, always-on-top, borderless window on this PC. Then the dashboard window, tray, autostart, single instance, the engine as a PyInstaller sidecar with the stdin handshake and parent watch, and the data-folder move. | Daily use with no terminal |
| **M5** | **Chibi:** sprite player, moods, bubbles, `lines.toml`, placeholder art | The chibi reacts to real events |
| **M6** | **Telegram:** `set-key telegram`, pairing, digest, alerts, rendering | The morning digest arrives on the phone |
| **M7** | **Installer and docs:** the Tauri bundler (NSIS installer), README, `CLAUDE.md` architecture and roadmap | A clean install on this PC works from the installer |

**Implementation plans are written one milestone at a time.** M1's plan comes first, and each later plan starts from what the previous milestone actually built.

**Review policy:**
- **Two-reviewer adversarial review** for M1 (Gmail, extraction concurrency), M2 (API security) and M6 (Telegram).
- **One review per milestone** for M3, M4, M5 and M7.
- Tests after every step and mutation checks on safety code, as now.

**M1 live check (2026-10-27), on the real mailbox:**
- **Backlog:** a fresh 7-day sync stored 90 emails; the expired 79 stayed expired. The pre-filter dropped none of the 90. The engine read all 90 in 2 minutes with 4 workers: 48 with opportunities, 42 without, every call answered. The first run stopped at its 60-call limit, and the next run a minute later took the rest.
- **Cost:** $0.0286 against a $0.036 estimate (worst case $0.97).
- **A new email:** a test internship email went from Gmail's receipt to a saved, ranked opportunity in **101 s** (target 150 s), through the change feed. It ranked first in `assistant opportunities` at 150.
- **Stopping:** one Ctrl+C let the running job finish and released `engine.lock`.
- **Privacy:** none of the 167 stored subjects appears in `engine.log`, and neither do email addresses or key-shaped text.
- **The 2-reviewer review** (44 agents, every finding re-checked by two skeptics) found 20 problems: 19 fixed test-first, 1 declined.

**The owner's tasks, each at its milestone:**
1. **Before M1 runs unattended:** publish the Google OAuth app (decision D6). In Testing mode, refresh tokens expire after 7 days, which would stop the engine weekly. *Status 2026-10-27: blocked on Google's side.* **Publish app** stays greyed out with "complete your configuration on the Branding page", even though the required Branding fields are saved. Other developers report the same on Google's developer forum. Until it works, run `assistant auth` once a week: the engine reports `auth_lost` and picks up the new sign-in without a restart.
2. **Before M4:** install Rust (`rustup`) and the Microsoft C++ build tools. Exact requirements are checked against Tauri's prerequisites page at that point. WebView2 ships with Windows 11; Node is already installed.
3. **Before M6:** create a bot with @BotFather and run `assistant set-key telegram`.
4. **Any time:** chibi art in the sprite format above.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Transparent, always-on-top, borderless windows misbehave on Windows. The docs found cover the parts separately, not the combination. | M4 opens with a spike on this PC before any chibi work. The fallback is a small borderless window with a solid rounded background. |
| A PyInstaller sidecar is large (about 30–50 MB) or flagged by antivirus | The `--onedir` build if needed. The installer is signed later if it becomes a problem. Measured in M4. |
| The Gmail bookmark expires early (the docs say "in rare cases a few hours") | The 404 fallback to the search sync (section 4) is tested |
| 4 extraction workers hit Anthropic's rate limit | A 429 stops the run without counting against the email (Step 8), and the next minute retries. The worker count is a setting. |
| The owner's OAuth app stays in Testing mode | `check_mail` reports auth lost clearly. Task 1 above. |

## 13. Out of scope

Each of these can become its own spec later:
- a cloud server or 24/7 running;
- AI chat with the chibi;
- an AI-written digest;
- editing the profile in the app;
- learning from star, dismiss and applied (they're recorded from day one);
- Google Calendar;
- a mobile app;
- Live2D;
- extra mail sources beyond Gmail.
