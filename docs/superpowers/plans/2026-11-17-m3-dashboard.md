# M3 Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The five-view dashboard from spec section 6, in the "Ink & Lamplight" look, running in the browser against `assistant engine --dev`.

**Architecture:** A Vite + React single-page app in `app/`. Every call goes through one `openapi-fetch` client typed from `app/src/api/schema.d.ts`; server state lives in TanStack Query; the engine's event stream (read with `fetch` streaming) invalidates queries so changes appear live. Two small engine additions come first (Task 1).

**Tech Stack:** React 19, TypeScript 5.9, Vite 8, Tailwind 4, shadcn/ui (Radix), TanStack Query 5, openapi-fetch, react-router 8 (hash router), Recharts 3, Vitest 5 + Testing Library + jsdom. Python/FastAPI for Task 1.

**Spec:** `docs/superpowers/specs/2026-11-17-m3-dashboard-design.md` (the binding M3 design), which builds on `docs/superpowers/specs/2026-10-25-desktop-app-redesign-design.md` sections 5 (API) and 6 (views and patterns).

## Global Constraints

- **Colours, exactly:** `ink #0F1319` page, `panel #181D25` raised surfaces, `mist #E6E9EE` text and primary-button fill, `mist-2 #949CA8` secondary text, `lamp #E6AE5C` accent, `alert #E07A6E` hard failures only. Tints: border `#333942`, hovered row `#22272F`, disabled `#5C636D`, lamp glow `#362F25`. No other colours anywhere (Task 2 adds a test that enforces it).
- **Amber only on:** the element under the pointer (1px edge + glow, never amber text), keyboard focus (2px ring + glow), the single top pick (a dot), the current sidebar page (2px edge), deadlines within 48 hours (amber text + clock), a short success glow. Never a large fill (Task 5 adds an allowlist test).
- **Alert red** always comes with a warning icon and words.
- **Dark only.** `<html class="dark">`, `color-scheme: dark`.
- **Type:** Georgia ("Iowan Old Style", serif fallback) for the greeting and big numbers only; `"Segoe UI", system-ui, sans-serif` elsewhere; `tabular-nums` for numbers in lists.
- **Password:** `sessionStorage` key `pocket.launchPassword`; never `localStorage`, never in a URL. A 401 anywhere returns to the password screen.
- **API base:** `import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:47615"`. Dev server `port: 5173, strictPort: true` (the engine's `--dev` CORS allows exactly `http://localhost:5173`).
- **Events:** `fetch` + `ReadableStream` with the `Authorization` header; never `EventSource`.
- **All API calls** go through `src/api/client.ts`; path strings are copied from `schema.d.ts`. After any engine API change: `assistant api-schema`, then `npm run gen:api` in `app/`.
- **Tests never call the real engine or the network:** `fetch` is injected or stubbed.
- **Routing:** `createHashRouter` (M4 serves static files from Tauri).
- **Shortcuts** never fire while typing in an input, textarea, select or the palette, nor with Ctrl/Alt/Meta held (except Ctrl+K).
- **Pinned versions** (all at least 14 days old, proven together in a scratch install), exact, no `^`:

  | dependencies | | devDependencies | |
  |---|---|---|---|
  | react, react-dom, react-is | 19.3.1 | typescript | 5.9.3 |
  | react-router | 8.5.1 | openapi-typescript | 7.13.0 |
  | @tanstack/react-query | 5.105.3 | vite | 8.3.5 |
  | openapi-fetch | 0.17.0 | @vitejs/plugin-react | 6.2.1 |
  | recharts | 3.10.1 | tailwindcss, @tailwindcss/vite | 4.3.4 |
  | cmdk | 1.1.1 | vitest | 5.1.0 |
  | radix-ui | 1.8.1 | jsdom | 30.1.3 |
  | lucide-react | 1.67.0 | @testing-library/react | 16.3.3 |
  | class-variance-authority | 0.7.1 | @testing-library/dom | 10.4.2 |
  | cn | 0.5.2 | @testing-library/user-event | 14.6.8 |
  | shadcn | 4.23.0 | @testing-library/jest-dom | 7.0.1 |
  | tw-animate-css | 1.4.0 | @types/react, @types/react-dom | 19.3.0 |
  | | | @types/node | 24.19.2 |

  `tsconfig` keeps `skipLibCheck: true` (jest-dom 7's matcher types clash with Vitest 5's otherwise). Commit `package-lock.json`. Install with `npm install --ignore-scripts`.
- **Commands** (from `app/`): `npm test` = `vitest run`; `npm run build` = `tsc -b && vite build`; `npm run check:api`. Python (from the worktree root): `../../.venv/Scripts/python -m pytest -q -p no:cacheprovider`. Every task ends with all three app commands green (from Task 2 on) and the Python suite green.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Long titles and Vietnamese text in dense rows** (60+ characters, diacritics): each row stays one line, truncates with an ellipsis and shows the full text on hover and in the panel. Test: Task 7, `long titles stay on one line with the full title on hover`.
2. **The PC sleeps, Wi-Fi drops or the engine restarts** while the dashboard is open: the stream reconnects with backoff and everything refetches; a new launch password sends the owner back to the password screen, not to a broken page. Tests: Task 4, `after the stream drops it reconnects and refetches everything`; Task 3, `a 401 later returns to the password screen`.
3. **Empty data** (a quiet day, a fresh database, a filter that matches nothing): an empty state with one line, never a blank table or a spinner that never ends. Tests: Task 8 `a quiet day shows the empty state`, Task 7 `an empty tab shows its empty state`, Task 9 `no emails in the window shows the empty state`.
4. **Shortcuts while typing or with modifiers** (typing "s" in the filter box, Ctrl+S, Ctrl+K inside the palette): only the intended action happens. Tests: Task 6, `shortcuts don't fire while typing`, `Ctrl+S does not star`.
5. **A browser clock in another time zone** (a laptop set to another time zone, Europe/Berlin in the test, or travel): "today", deadlines and day groups still follow Ho Chi Minh City. Test: Task 5, `the browser's time zone doesn't change any label`.

---

### Task 1: Engine additions: "new today" and the profile path

**Files:**
- Modify: `src/assistant/engine/api_deps.py` (move `utc_text` here from `api_overview.py`), `src/assistant/engine/api_opportunities.py` (`list_opportunities`), `src/assistant/engine/api_overview.py` (`today`, `Settings`, `settings`)
- Regenerate: `app/src/api/openapi.json`, `app/src/api/schema.d.ts`
- Test: `tests/test_api_opportunities.py`, `tests/test_api_overview.py`

**Interfaces:**
- Produces: `GET /api/opportunities?new_today=true` → open items (the tab's own condition still applies; use with `tab=all`) whose `first_seen_at` falls within today's HCMC day. `/api/today` `counts.new_today` counts `OPEN` items with the same bounds. `GET /api/settings` gains `profile_path: str` (`str(ctx.cfg.profile_path)`).

- [ ] **Step 1: Write the failing tests**

```python
def test_new_today_lists_open_items_first_seen_today_in_hcmc(api_at):  # tests/test_api_opportunities.py
    api = api_at(datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc))  # 00:30 on the 28th in HCMC
    seed(api, "New open", first_seen_at="2026-10-27 17:10:00")
    seed(api, "New dismissed", status="dismissed", first_seen_at="2026-10-27 17:10:00")
    seed(api, "Yesterday", first_seen_at="2026-10-27 16:50:00")
    assert titles(api, tab="all", new_today="true") == ["New open"]
    assert api.get("/api/today")["counts"]["new_today"] == 1

def test_settings_name_the_profile_file(api):  # tests/test_api_overview.py
    assert api.get("/api/settings")["profile_path"] == str(api.cfg.profile_path)
```

(`api_at` lives in `tests/test_api_overview.py`; import it, or move it to `tests/api_helpers.py` and import from there in both files.)

- [ ] **Step 2: Run them to confirm they fail** — `new_today` is ignored (3 titles) and `profile_path` is missing.
- [ ] **Step 3: Implement.** Add `new_today: bool = False` to `list_opportunities`; when true, add `first_seen_at >= :start AND first_seen_at < :end` with `utc_text(today)` / `utc_text(today + 1 day)`. Change the Today count to `f"{OPEN} AND first_seen_at >= :start AND first_seen_at < :end"`. Add `profile_path` to `Settings`.
- [ ] **Step 4: Regenerate the contract:** `PYTHONPATH=src ../../.venv/Scripts/python -m assistant.cli api-schema`, then in `app/`: `npm run gen:api`.
- [ ] **Step 5: Run the Python suite and `npm run check:api`.** Expected: all pass (the contract test sees the new document).
- [ ] **Step 6: Commit:** `Filter today's new items and name the profile file`

---

### Task 2: Scaffold the app with the Ink & Lamplight tokens

**Files:**
- Modify: `app/package.json` (scripts, pinned deps), `.gitignore` (`app/dist/`)
- Create: `app/index.html`, `app/vite.config.ts`, `app/tsconfig.json`, `app/tsconfig.app.json`, `app/tsconfig.node.json`, `app/components.json`, `app/src/main.tsx`, `app/src/index.css`, `app/src/lib/utils.ts`, `app/src/tokens.ts`, `app/src/test-setup.ts`, `app/src/components/ui/*` (generated), `app/src/tokens.test.ts`, `app/src/no-stray-colours.test.ts`

**Interfaces:**
- Produces: `src/tokens.ts` exports `PALETTE = { ink, panel, mist, mist2, lamp, alert, border, hover, disabled, glow }` (hex strings, the Global Constraints values), for charts and tests. CSS variables `--ink --panel --mist --mist-2 --lamp --lamp-glow --alert --line --hover --disabled` and Tailwind colours `bg-ink bg-panel text-mist text-mist-2 text-lamp text-alert border-line bg-hover text-disabled` (via `@theme inline`). The `@/` alias to `src/`.

- [ ] **Step 1: Write the app skeleton by hand** (create-vite's react-ts template files, without `App.css`, `assets/`, oxlint, and with TypeScript 5.9.3): `index.html` with `<html lang="en" class="dark">` and title "Pocket Assistant"; `vite.config.ts` with `react()`, `tailwindcss()`, `resolve.alias {"@": path.resolve(import.meta.dirname, "./src")}`, `server {port: 5173, strictPort: true}`, and `test {environment: "jsdom", setupFiles: ["./src/test-setup.ts"]}` (with `/// <reference types="vitest/config" />`); the `@/*` path in both `tsconfig.json` and `tsconfig.app.json`; `package.json` scripts `dev`, `build`, `test`, `gen:api`, `check:api` and the exact pins above. Run `npm install --ignore-scripts`.
- [ ] **Step 2: Add shadcn:** `npx shadcn@4.23.0 init -b radix -p nova --no-monorepo --no-rtl`, then `npx shadcn@4.23.0 add button command dialog tabs tooltip -y`. Remove the Geist font import from `index.css` and `@fontsource-variable/geist` from `package.json`. Re-pin anything shadcn added with `^` to the exact versions above. Expected: `npm ls` shows no missing or invalid peers.
- [ ] **Step 3: Write the failing tests**

```ts
// src/tokens.test.ts
import css from "./index.css?raw";
import { PALETTE } from "./tokens";
test("index.css defines each palette colour with its exact value", () => {
  for (const [name, hex] of [["ink", PALETTE.ink], ["panel", PALETTE.panel], ["mist", PALETTE.mist], ["mist-2", PALETTE.mist2],
       ["lamp", PALETTE.lamp], ["alert", PALETTE.alert], ["line", PALETTE.border], ["hover", PALETTE.hover], ["lamp-glow", PALETTE.glow]])
    expect(css).toMatch(new RegExp(`--${name}:\\s*${hex}`, "i"));
});
test("shadcn's own tokens point at the palette", () => {
  expect(css).toMatch(/--background:\s*var\(--ink\)/); expect(css).toMatch(/--ring:\s*var\(--lamp\)/);
  expect(css).toMatch(/--destructive:\s*var\(--alert\)/); expect(css).toMatch(/--primary:\s*var\(--mist\)/);
});
// src/no-stray-colours.test.ts — our code (not components/ui, tokens.ts, index.css, tests) never writes a raw colour
test("no raw hex colours or Tailwind palette colours outside the tokens", () => {
  const files = import.meta.glob(["./**/*.{ts,tsx}", "!./components/ui/**", "!./tokens.ts", "!./**/*.test.*"], { query: "?raw", import: "default", eager: true });
  for (const [path, text] of Object.entries(files))
    expect([path, (text as string).match(/#[0-9a-f]{3,8}\b|\b(?:bg|text|border|ring|fill|stroke|shadow)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/gi)]).toEqual([path, null]);
});
```

- [ ] **Step 4: Run them** (`npm test`) — fail: the tokens don't exist yet.
- [ ] **Step 5: Implement the tokens:** in `index.css`, replace shadcn's `:root` and `.dark` blocks with one `:root` block holding our variables and shadcn's mapped to them (background→ink, foreground→mist, card/popover→panel with mist foregrounds, primary→mist with ink foreground, secondary/muted/accent→hover with mist / mist-2 foregrounds, destructive→alert, border/input→line, ring→lamp, chart-1..5 → mist, mist-2, line, lamp, alert, sidebar-* likewise); expose ours in `@theme inline`; set `--font-sans` and `--font-serif` from Global Constraints; `body` is `bg-ink text-mist`. Write `tokens.ts` and `test-setup.ts` (jest-dom import, `cleanup` after each, `ResizeObserver` and `scrollIntoView` stubs). `main.tsx` renders a placeholder.
- [ ] **Step 6: Verify:** `npm test`, `npm run build`, `npm run check:api` — all pass.
- [ ] **Step 7: Commit:** `Scaffold the dashboard with the Ink & Lamplight tokens`

---

### Task 3: The API client, the password screen, and "engine not running"

**Files:**
- Create: `app/src/api/client.ts`, `app/src/api/keys.ts`, `app/src/api/auth.tsx`, `app/src/App.tsx`, `app/src/views/Login.tsx`, `app/src/views/Unreachable.tsx`
- Modify: `app/src/main.tsx` (QueryClientProvider, TooltipProvider, AuthProvider, App)
- Test: `app/src/api/client.test.ts`, `app/src/api/auth.test.tsx`

**Interfaces:**
- Produces:
  - `API_BASE: string`; `makeClient(opts: { fetch?: typeof fetch; getPassword: () => string | null; onUnauthorized: () => void }): Client<paths>`; `class ApiError extends Error { status: number }`; `unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T>` (throws `ApiError` for non-2xx).
  - `keys` in `keys.ts`: `today`, `opportunities(params)`, `opportunity(id)`, `tabCount(tab)`, `emails(params)`, `email(id)`, `search(q)`, `spend(days)`, `digests`, `items(weeks)`, `settings` (arrays starting with the resource name, so `invalidateQueries({queryKey: ["opportunities"]})` matches all lists).
  - `auth.tsx`: `PASSWORD_KEY = "pocket.launchPassword"`; `AuthProvider`; `useAuth(): { password: string | null; client: Client<paths>; signIn(p: string): Promise<"ok" | "wrong" | "unreachable">; signOut(): void }` — `signIn` tries `GET /api/settings` with the candidate.
  - `App`: no password → `Login`; `GET /api/health` failing → `Unreachable` (retries every 3 s); otherwise the router (Task 6).
- Copy: Login — "Pocket Assistant" / "Paste the launch password printed by `assistant engine --dev`." / error "That password didn't work. Copy it again from the engine's window." Unreachable — "Can't reach the engine at 127.0.0.1:47615." / "Start it with `assistant engine --dev`." / "Retrying…".

- [ ] **Step 1: Write the failing tests**

```ts
test("adds the bearer header"); // fake fetch records Authorization === "Bearer pw"
test("a 401 calls onUnauthorized and unwrap throws ApiError 401");
test("unwrap throws ApiError with the status on a 500");
test("a wrong password says so and stores nothing");       // localStorage.length === 0, sessionStorage empty
test("a right password is kept in sessionStorage only");   // sessionStorage.getItem(PASSWORD_KEY) === "pw", localStorage.length === 0
test("a 401 later returns to the password screen");        // Review Focus 2
test("an unreachable engine shows the start command, then the app once it answers"); // fake timers, fetch rejects then resolves
```

- [ ] **Step 2: Run them** — fail (modules missing).
- [ ] **Step 3: Implement** as the Interfaces say (openapi-fetch middleware: `onRequest` sets the header, `onResponse` on 401 calls `onUnauthorized`).
- [ ] **Step 4: Verify** (`npm test`, `npm run build`, `npm run check:api`).
- [ ] **Step 5: Commit:** `Talk to the engine with the launch password`

---

### Task 4: Live updates from the event stream

**Files:**
- Create: `app/src/api/events.ts`, `app/src/api/useLiveEvents.ts`
- Modify: `app/src/App.tsx` (mount `useLiveEvents` once signed in)
- Test: `app/src/api/events.test.ts`

**Interfaces:**
- Consumes: `API_BASE`, `useAuth`, `keys` (Task 3).
- Produces:
  - `readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }>` — the WHATWG rules: CRLF/LF/CR, `:` comments ignored (`: ping`), one leading space stripped, multi-line data joined with `\n`, blank line dispatches, an unterminated event at the end is dropped, `TextDecoder` with `{stream: true}`.
  - `type EngineEvent = { kind: "items_new"; new: number; merged: number } | { kind: "item_changed"; id: number; action: "star" | "unstar" | "dismiss" | "restore" | "applied" } | { kind: "ranking_changed"; rescored: number } | { kind: "status"; source: string; problem: string | null; [detail: string]: unknown }` (from `src/assistant/engine/events.py`); `parseEvent(e): EngineEvent | null` (unknown kinds and bad JSON → null).
  - `invalidationFor(e: EngineEvent): QueryKey[]`: `items_new` → opportunities, tab counts, today, emails; `item_changed` → opportunities, opportunity(id), tab counts, today; `ranking_changed` → opportunities, tab counts, today; `status` → today, settings.
  - `connectEvents(opts: { fetch; url; getPassword; onEvent(e: EngineEvent); onReconnect(); onUnauthorized(); signal: AbortSignal }): void` — reconnects after the stream ends or errors with backoff 1 s, 2 s, 5 s, then every 10 s; calls `onReconnect` after each successful reconnect (not the first connect); a 401 calls `onUnauthorized` and stops.
  - `useLiveEvents()`: wires `connectEvents` to `queryClient.invalidateQueries` per `invalidationFor`, and on reconnect invalidates everything.

- [ ] **Step 1: Write the failing tests**

```ts
test("reads events split across chunks");       // "event: items_new\nda" + "ta: {\"new\": 2, \"merged\": 0}\n\n"
test("a CRLF split across chunks is one line break");
test("ping comments are ignored");
test("unknown kinds and bad JSON are dropped");
test("item_changed refreshes that item, the lists, the counts and Today");
test("after the stream drops it reconnects and refetches everything"); // Review Focus 2: fake timers; 1st fetch's stream ends, 2nd connects after 1 s, onReconnect called once
test("a 401 on the stream signs out and stops reconnecting");
```

- [ ] **Step 2: Run them** — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Refresh the dashboard from the engine's live events`

---

### Task 5: What deadlines, scores, kinds and problems look like

**Files:**
- Create: `app/src/lib/time.ts`, `app/src/lib/score.ts`, `app/src/lib/kinds.ts`, `app/src/lib/status.ts`, `app/src/components/Deadline.tsx`, `app/src/components/ScoreBar.tsx`, `app/src/components/KindPill.tsx`, `app/src/components/TopPick.tsx`, `app/src/components/StatusLine.tsx`, `app/src/components/EmptyState.tsx`, `app/src/lamp-allowlist.test.ts`
- Modify: `app/src/index.css` (utilities `lamp-hover` = 1px lamp edge + 4px glow on hover; `lamp-focus` = 2px lamp ring + glow on `:focus-visible`), `app/src/components/ui/button.tsx` (variants `primary` = mist fill / ink text, `secondary` = `border-line` outline; both `lamp-hover lamp-focus`; disabled `text-disabled`)
- Test: `app/src/lib/*.test.ts`, `app/src/components/*.test.tsx`

**Interfaces:**
- Produces:
  - `time.ts`: `HCMC = "Asia/Ho_Chi_Minh"`; `hcmcDate(now: Date): string` (YYYY-MM-DD in HCMC); `deadlineInfo(deadline: string | null, now: Date): { label: string; tone: "overdue" | "urgent" | "week" | "later" | "none" }` — a deadline D ends at D 23:59:59 +07:00; d = D minus today (HCMC days); d < 0 → "overdue"/overdue; d = 0 → "in Nh" (hours to the end, rounded up; "in Nm" under an hour); d = 1 → "tomorrow"; d ≥ 2 → "in N days"; tone urgent when the end is ≤ 48 h away, week when d ≤ 6, else later; null → "no deadline"/none. `greeting(now)`: HCMC hour 5–11 "Good morning", 12–17 "Good afternoon", else "Good evening". `dayGroup(iso, now)`: "Today", "Yesterday", else e.g. "Mon 16 Nov". `clock(iso)`: "21:42" in HCMC.
  - `score.ts`: `HIGH = 75`, `MEDIUM = 50`; `segments(score): 1 | 2 | 3` (≥75 → 3, ≥50 → 2, else 1).
  - `kinds.ts`: `type Kind = "hackathon" | "ideathon" | "competition" | "internship" | "job" | "job_fair" | "seminar" | "uni_notice" | "other"`; `KINDS: Record<Kind, { label: string; Icon: LucideIcon }>` — Zap, Lightbulb, Trophy, GraduationCap, Briefcase, Users, Presentation, Bell, Dot; labels "Hackathon", "Ideathon", "Competition", "Internship", "Job", "Job fair", "Seminar", "University notice", "Other"; `kindOf(s: string): Kind` (unknown → "other").
  - `status.ts`: `describeProblem(source: string, problem: string, details: Record<string, unknown>): { text: string; fix?: string; severity: "alert" | "quiet" }` — gmail `auth_lost` alert "Gmail sign-in expired" fix `assistant set-key gmail`; gmail `offline` quiet "Offline since {clock(last_ok_check_at)} · retrying"; gmail `gmail_busy` quiet "Gmail is limiting checks · retrying"; gmail `untrusted_network` alert "Gmail's certificate didn't check out on this network"; anthropic `busy`/`no_answer`/`offline` quiet "Anthropic is busy · retrying"; anthropic `cap_daily` quiet "Today's AI budget is used up · resumes at midnight"; anthropic `anthropic` alert "Anthropic refused the request" fix `assistant set-key anthropic`; `profile` alert "profile.toml has a mistake" + details.detail; `job:*` `job_error` alert "A background job failed ({detail})"; anything else quiet with the raw problem name.
  - Components: `<Deadline deadline now />` (urgent → `text-lamp` + Clock icon; overdue → bold mist; week → mist; later/none → mist-2), `<ScoreBar score />` (number + 3 segments: mist / mist-2 / line), `<KindPill kind />` (outlined, icon + label), `<TopPick on />` (lamp dot or an equal-width blank), `<StatusLine status problems />` (online dot + "Checked {clock} · Online", or the worst problem; alert ones with AlertTriangle in `text-alert`; spend text passed in), `<EmptyState line />` (a quiet placeholder figure where the chibi will go, plus the line).

- [ ] **Step 1: Write the failing tests**

```ts
test("a deadline later today shows hours left and is urgent");   // deadlineInfo("2026-11-18", new Date("2026-11-17T21:00:00Z")) → {label: "in 20h", tone: "urgent"}
test("HCMC midnight decides what 'today' is");                   // hcmcDate(2026-11-17T16:59:59Z) = "2026-11-17"; at 17:00:00Z = "2026-11-18"
test("tomorrow is urgent only inside 48 hours");                  // ("2026-11-19", 2026-11-17T17:30Z) → {label: "tomorrow", tone: "urgent"} (47.5 h left); ("2026-11-19", 2026-11-16T10:00Z) → {label: "in 3 days", tone: "week"}
test("past deadlines are overdue and far ones are 'later'");
test("the browser's time zone doesn't change any label");          // Review Focus 5: process.env.TZ = "Europe/Berlin" then "America/Los_Angeles": same results
test("score segments: 75 → 3, 74 → 2, 50 → 2, 49 → 1");
test("every engine kind has a label and its own icon");           // the 9 kinds from src/assistant/models.py; Icons all distinct
test("status words: auth_lost is an alert with a fix; offline is quiet with the time");
test("Deadline: urgent is lamp with a clock; later is mist-2");
// src/lamp-allowlist.test.ts — the anti-goal: amber stays rare
test("lamp classes appear only in the allowed files"); // /\b(text|bg|border|ring|shadow|fill|stroke)-lamp\b|--lamp\b/ only in: index.css, tokens.ts, components/Deadline.tsx, components/TopPick.tsx, components/Shell.tsx, components/Toast.tsx (and tests)
```

- [ ] **Step 2: Run them** — fail.
- [ ] **Step 3: Implement** (use `Intl.DateTimeFormat` with `timeZone: HCMC`; never `getHours()`/`getDate()` on local time).
- [ ] **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Show deadlines, scores, kinds and problems in the palette`

---

### Task 6: Shell, routes, keyboard and the Ctrl+K palette

**Files:**
- Create: `app/src/router.tsx`, `app/src/components/Shell.tsx`, `app/src/components/Toast.tsx`, `app/src/lib/keys.ts`, `app/src/components/CommandPalette.tsx`, placeholder `app/src/views/{Today,Opportunities,Emails,Reports,Settings}.tsx`
- Modify: `app/src/App.tsx` (render the router)
- Test: `app/src/lib/keys.test.tsx`, `app/src/components/CommandPalette.test.tsx`, `app/src/components/Shell.test.tsx`

**Interfaces:**
- Consumes: `useAuth`, `keys`, `unwrap` (Task 3); `EmptyState`, `kindOf`/`KINDS` (Task 5).
- Produces:
  - Routes (hash): `/` Today; `/opportunities?tab=&kind=&within=&min=&q=&new=1&id=`; `/emails?section=&id=`; `/reports`; `/settings`. Search params are the single source of truth for filters and the open panel.
  - `Shell`: sidebar (brand with a lamp mark, nav Today / Opportunities (For you count) / Emails / Reports / Settings, the current page with a 2px lamp edge, a "Search · Ctrl K" button at the bottom), the page outlet, the toast area.
  - `toast(message: string, kind: "done" | "alert")` from `Toast.tsx`: done = mist text with a short lamp glow; alert = AlertTriangle + `text-alert`; gone after 4 s.
  - `keys.ts`: `isTypingTarget(el: EventTarget | null): boolean`; `useShortcuts(map: Record<string, () => void>, enabled = true)` — keys like `"j"`, `"Enter"`, `"Escape"`, `"/"`, and two-key sequences `"g t"` (the second key within 1 s); ignored while typing or with Ctrl/Alt/Meta. Global map in `Shell`: `g t` → `/`, `g o` → `/opportunities`, `g e` → `/emails`, `g r` → `/reports`, `g s` → `/settings`, `/` and Ctrl+K → the palette.
  - `CommandPalette`: `CommandDialog` with `<Command>` inside (shadcn 4.23 needs both); queries `GET /api/search` (debounced 150 ms, from 2 characters); groups "Opportunities", "Emails", "Pages", each item showing its type; Enter navigates (opportunity → `/opportunities?tab=all&id={id}`, email → `/emails?id={id}`, page → its route); a bottom row lists `↵ open · esc close · g o / g e / g r jump`.

- [ ] **Step 1: Write the failing tests**

```ts
test("g o goes to Opportunities and g e to Emails");
test("a sequence times out after 1 second");                 // fake timers: "g", wait 1.1 s, "o" → nothing
test("shortcuts don't fire while typing");                   // Review Focus 4: focus an <input>, type "g o" and "s" → no navigation, handler not called
test("Ctrl+S does not star");                                 // Review Focus 4
test("Ctrl+K opens the palette; typing shows results with their type");
test("Enter on an opportunity opens it in the panel route");
test("the current page has the lamp edge and the others don't");
```

- [ ] **Step 2: Run them** — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Add the shell, keyboard and the Ctrl+K palette`

---

### Task 7: Opportunities, the side panel, and actions

**Files:**
- Create: `app/src/api/useAct.ts`, `app/src/views/OpportunityPanel.tsx`
- Modify: `app/src/views/Opportunities.tsx`
- Test: `app/src/views/Opportunities.test.tsx`, `app/src/api/useAct.test.tsx`

**Interfaces:**
- Consumes: routes and `useShortcuts` (Task 6), Task 5 components, `keys`.
- Produces:
  - `useAct(): (id: number, action: "star" | "unstar" | "dismiss" | "restore" | "applied") => void` — optimistic: patches every cached opportunities list (dismiss/applied remove the row from For you and All; star toggles `starred`) and `opportunity(id)`; on error rolls back and toasts `alert` "Couldn't {verb} it. Try again."; on success toasts `done` ("Marked as applied", "Dismissed", "Starred", "Unstarred", "Restored"); settles by invalidating opportunities, tab counts and Today.
  - Page: tabs For you · All · Starred · Applied · Dismissed with counts (`GET /api/opportunities?tab=X&limit=1` → `tab_total`, one query per tab); filter chips (kind: any + the 9 kinds; deadline: any, 3, 7, 14, 30 days; minimum score: any, 50, 60, 75; a text filter debounced 200 ms) written to the search params; "{tab_total − total} hidden by filters · Clear" when filters hide anything; dense rows: `TopPick` (first row of For you only), `ScoreBar`, `KindPill`, title (one line, ellipsis, `title=` full text) over "organizer · why" (mist-2, one line), "seen ×N" when N > 1, `Deadline`, and on hover/selection the Dismiss and Applied buttons (Restore instead of Dismiss in the Dismissed tab); row click or Enter opens `?id=`; `j`/`k` move the selection, `s` star/unstar, `d` dismiss, `a` applied on the selected row; Esc closes the panel.
  - `OpportunityPanel` (inline on the right, ~360px, `bg-panel`): kind, title, organizer · place · `Deadline`; the big serif score with `ScoreBar` and the breakdown lines (label, ±points); eligibility; prize or pay; buttons Open link (`url`, new tab), Star/Unstar, Dismiss/Restore, Applied; "Seen in N emails" with each sighting's subject, sender and **Open in Gmail** (`gmail_url`, new tab); `flagged` items show "This email tried to give the assistant instructions" with AlertTriangle in alert red.

- [ ] **Step 1: Write the failing tests**

```ts
test("tabs show counts from each tab's total");
test("filters hide items and Clear brings them back");          // "2 hidden by filters · Clear"
test("Applied disappears from For you at once and comes back if the call fails"); // fake fetch 500 → row back + "Couldn't mark it applied. Try again."
test("the panel shows the breakdown and each sighting's Gmail link");
test("Esc closes the panel");
test("j, j, a marks the second row applied");
test("long titles stay on one line with the full title on hover"); // Review Focus 1: 90-char Vietnamese title → has `truncate` and title attribute equal to the full text
test("an empty tab shows its empty state");                     // Review Focus 3: "No opportunities here yet."
```

- [ ] **Step 2: Run them** — fail. **Step 3: Implement.** **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Build Opportunities with the side panel and live actions`

---

### Task 8: Today

**Files:**
- Modify: `app/src/views/Today.tsx`
- Test: `app/src/views/Today.test.tsx`

**Interfaces:**
- Consumes: `GET /api/today` (`counts`, `top_picks`, `needs_action`, `upcoming_deadlines`, `spend`, `status`); Task 5 components; `toast`; `useAct` (Task 7) for the inline Dismiss and Applied buttons on top picks.
- Produces: the page as spec section 6 and the design doc describe: serif greeting (`greeting(now)`) and one line ("{n} things need you today." where n = top picks shown + emails needing action; "Nothing needs you today." when 0); four cards (serif numbers) linking New today → `/opportunities?tab=all&new=1`, Deadlines this week → `/opportunities?tab=all&within=6`, Starred → `?tab=starred`, Applied → `?tab=applied`; "For you today" = top picks (first with `TopPick`; each with inline Dismiss and Applied on hover, a click opens `/opportunities?tab=all&id=`) then emails needing action (subject, action note, link to `/emails?id=`); "Upcoming deadlines" (day label + title, urgent ones lamp); the status line with "${spend.month_usd} of ${spend.monthly_soft_cap_usd} this month" (two decimals).

- [ ] **Step 1: Write the failing tests**

```ts
test("the four cards show the counts and open their lists");   // hrefs exactly as above
test("only the first top pick gets the lamp dot");
test("emails that need action show their note and open the email");
test("the status line shows the last check and the month's spend"); // "Checked 21:42 · Online" and "$0.08 of $5.00 this month"
test("a Gmail sign-in problem shows in alert red with the fix");
test("a quiet day shows the empty state");                      // Review Focus 3: no picks, no emails → "Nothing needs you today."
```

- [ ] **Step 2: Run them** — fail. **Step 3: Implement.** **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Build the Today view`

---

### Task 9: Important emails

**Files:**
- Create: `app/src/views/EmailPane.tsx`
- Modify: `app/src/views/Emails.tsx`
- Test: `app/src/views/Emails.test.tsx`

**Interfaces:**
- Consumes: `GET /api/emails?section&days`, `GET /api/emails/{message_id}`; `dayGroup`, `clock`, `KindPill`, `EmptyState`, `useShortcuts`.
- Produces: tabs Important · Other (`?section=`), a days chip (7 / 14 / 30, default 7); rows grouped by `dayGroup`: sender name, subject (one line, bold), excerpt (mist-2, one line), badge pills (opportunity, university, needs action, addressed the assistant; outlined, icon + word, "addressed the assistant" in alert red with AlertTriangle), time; "Showing the newest 500 in this window." when `truncated`; `EmailPane` from `?id=`: subject, sender, time, badges, excerpt, action note, the opportunities found (rows that open `/opportunities?tab=all&id=`), **Open in Gmail**; `j`/`k`/Enter/Esc as on Opportunities.

- [ ] **Step 1: Write the failing tests**

```ts
test("emails are grouped by Ho Chi Minh City day");             // Today / Yesterday / "Mon 16 Nov"
test("the Other tab asks for section=other");
test("the reading pane shows the note, the opportunities found and Open in Gmail");
test("a truncated window says so");
test("no emails in the window shows the empty state");          // Review Focus 3: "No important email in the last 7 days."
```

- [ ] **Step 2: Run them** — fail. **Step 3: Implement.** **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Build the Important emails view`

---

### Task 10: Reports

**Files:**
- Create: `app/src/lib/series.ts`
- Modify: `app/src/views/Reports.tsx`
- Test: `app/src/lib/series.test.ts`, `app/src/views/Reports.test.tsx`

**Interfaces:**
- Consumes: `GET /api/reports/spend?days=30`, `/api/reports/digests`, `/api/reports/items?weeks=8`; `PALETTE`.
- Produces: `fillDays(days: SpendDay[], lastDay: string, n: number): SpendDay[]` (n consecutive HCMC days ending at `lastDay`, zeros where the API sent nothing). Page: "Spend" — the month total against the cap as text ("$0.88 of $5.00 this month") and a thin budget bar (fill mist, track line); daily bars (Recharts `BarChart`, `isAnimationActive={false}`, past days `PALETTE.mist2`, today `PALETTE.mist`), tokens in/out as small numbers; "Digests" — the list or the empty line "No digests yet. They start with Telegram."; "Items per week" — a table, one row per week, columns per kind and per source (counts, `tabular-nums`).

- [ ] **Step 1: Write the failing tests**

```ts
test("fillDays fills quiet days with zeros and keeps n days");  // 3 sparse days over n=30 → length 30, zeros between
test("the month's spend reads against the cap");
test("no digests shows the empty line");
test("items per week list each week's kinds and sources");
```

- [ ] **Step 2: Run them** — fail. **Step 3: Implement.** **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Build the Reports view`

---

### Task 11: Settings

**Files:**
- Modify: `app/src/views/Settings.tsx`
- Test: `app/src/views/Settings.test.tsx`

**Interfaces:**
- Consumes: `GET /api/settings` (with `profile_path` from Task 1), `POST /api/settings/rescore`, `toast`, `describeProblem`.
- Produces: "Connected accounts" rows (Railway-style): Gmail (address; "Signed in" with a Check, or "Sign-in expired" in alert red with AlertTriangle and a copyable `assistant set-key gmail`), Anthropic ("Key saved" or "No key yet" with `assistant set-key anthropic`), Telegram ("Arrives with Telegram", no button). "Limits" (read-only): monthly soft cap and daily cap (`[llm] monthly_soft_cap_usd`, `[llm] daily_cap_usd`), "For you" minimum score (`[dashboard] for_you_min_score`), each with its `config.toml` key and "Restart the engine after changing config.toml." "Profile": level, interests, open to, home, remote; the `profile_path` with a Copy button (`navigator.clipboard.writeText`); **Rescore** → toast `done` "Rescored {rescored} · {expired} expired".

- [ ] **Step 1: Write the failing tests**

```ts
test("Gmail shows signed in, or the expired state with the command to fix it");
test("Telegram has no button yet");
test("limits are read-only and name their config.toml keys");
test("Copy puts the profile path on the clipboard");
test("Rescore reports what it did");
```

- [ ] **Step 2: Run them** — fail. **Step 3: Implement.** **Step 4: Verify.**
- [ ] **Step 5: Commit:** `Build the Settings view`

---

### Task 12: Look check on the real engine, and the README

**Files:**
- Modify: `README.md` (a "Using the dashboard (M3)" section), any view or token the check corrects
- Test: tests for any behaviour the check changes

- [ ] **Step 1: Run it for real.** Stop the owner's daily engine (its terminal tab), start this branch's engine in dev mode in a terminal tab: `$env:PYTHONPATH = '<worktree>\src'; & '<repo>\.venv\Scripts\python.exe' -m assistant.cli --config '<repo>\config.toml' engine --dev`; in another tab, `npm run dev` in `app/`. Open `http://localhost:5173` in the built-in browser and paste the printed password.
- [ ] **Step 2: Walk every view and shortcut** against the design doc and the direction-A mockups (`.superpowers/brainstorm/` in the main checkout): Today, each Opportunities tab with filters and the panel, an action and its toast, Emails with the pane, Reports, Settings, Ctrl+K, `g` jumps, a star seen live in a second browser tab. Screenshot each view. Note every gap.
- [ ] **Step 3: Fix the gaps**, each behavioural fix with a test that failed first; token and spacing fixes need none.
- [ ] **Step 4: README:** start the engine with `--dev`, then in `app/`: `npm install --ignore-scripts` (first time), `npm run dev`, open `http://localhost:5173`, paste the password; the keyboard list; that the password lasts until the tab closes.
- [ ] **Step 5: Verify** (all app commands, the Python suite).
- [ ] **Step 6: Commit:** `Check the dashboard on the real engine and document it`
- [ ] **Step 7: Hand over to the owner** to use it daily and gather feedback; the engine keeps running from this branch in dev mode until M3 merges.
