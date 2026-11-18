# M3: the dashboard, in the browser

**Status:** design, 2026-11-17. Builds on the redesign spec (`2026-10-25-desktop-app-redesign-design.md`): its section 6 defines the views and shared patterns, section 5 the API. This document adds what M3 needs on top of those: the look the owner chose, and how the dashboard is built and run before the desktop app exists.

## Intent

The owner opens this every day to see what needs them: new opportunities, close deadlines, emails that ask for action. The owner wants a **calm, focused, night-study feel**. The owner will use the finished dashboard first and give detailed feedback after; M3 must therefore be complete enough to live with daily, and easy to restyle afterwards.

## Goals

- All five views from spec section 6 (Today, Opportunities, Important emails, Reports, Settings), the right-hand details panel, the Ctrl+K palette and the keyboard map, working against the real engine.
- The look below, applied everywhere, from one set of design tokens, so later feedback changes tokens, not components.
- Live updates: a star, a new item or a status change shows up within about 2 seconds without a refresh (spec success criterion).
- Runs in the browser against `assistant engine --dev` on the PC.

## Non-goals (later milestones)

- The desktop window, tray, autostart and the launch-password handshake (M4). The laptop's viewer mode, connection banner and offline snapshot (M4).
- Chibi art, moods and bubbles (M5). Empty states get a placeholder where the chibi will go.
- Telegram pairing (M6): its Settings row shows "Arrives with Telegram" and no button.
- A light theme, phone layouts.

## Anti-goals

- A busy or colourful screen. If amber starts appearing everywhere, the design has failed even if it works.
- Keeping the launch password anywhere that outlives the browser tab.
- Showing sender email addresses or mail bodies (the API never returns them; the UI must not try).

## Approaches considered

1. **The spec's stack: a Vite + React + TypeScript single-page app in `app/`, styled with Tailwind and shadcn/ui, calling the API through a client typed from `app/src/api/schema.d.ts`.** Chosen (builder's call; the owner left the how to the builder). M4 wraps exactly this build in Tauri, and the typed contract from M2 catches API drift at compile time.
2. Pages rendered by the engine itself (Jinja + htmx). No build step, but M4's desktop window wants a static app, and it throws away the typed contract. Rejected.
3. Another framework (Svelte, Solid). Fine tools, but the spec, shadcn/ui and the M2 contract tooling are React-first. Rejected.

## The look (decided with the owner)

Direction **A, "Ink & Lamplight"**, chosen from three Mobbin-grounded options: a cool blue-grey room with one warm desk lamp. Five colours, two families plus one opposite accent:

| Token | Hex | Role |
|---|---|---|
| `ink` | `#0F1319` | page background |
| `panel` | `#181D25` | raised surfaces: cards, side panel, palette, menus |
| `mist` | `#E6E9EE` | primary text; the fill of primary buttons |
| `mist-2` | `#949CA8` | secondary text; the source of every border, hover and disabled tint |
| `lamp` | `#E6AE5C` | the accent: highlights, hovers, focus, effects only |

Plus one exception the owner approved: **`alert` `#E07A6E`**, a soft red used only for hard failures (Gmail sign-in expired, a check failed), always with a warning icon and words.

Derived tints (from the five, never new hues): border = `mist-2` at 22% over `panel` (`#333942`); hovered row = `mist` at 5% over `panel` (`#22272F`); disabled = `mist-2` at 55% (`#5C636D`); accent glow = `lamp` at 18% (`#362F25`).

Measured contrast (WCAG 2.x): mist on ink 15.3, on panel 13.9; mist-2 on ink 6.7, on panel 6.1; lamp on ink 9.4 (also passes as text). Borders are decorative hairlines (1.45, exempt); disabled is exempt.

**Rules for the accent.** Amber appears only on: the element under the pointer (a 1px edge plus a soft glow, never amber text), keyboard focus (2px ring plus glow), the single top pick (a dot), the current page in the sidebar (a 2px edge), deadlines within 48 hours (amber text with a clock icon), and a short glow when an action succeeds. Never a large filled area.

**Meaning without extra colours.**
- Deadlines: overdue = bold mist with the word "overdue"; within 48 h = amber with a clock and a relative label ("in 20h"); within 7 days = mist; later = mist-2. Deadlines are dates; a deadline of day D means the end of D in Ho Chi Minh City time (23:59 +07), and relative labels count from now in that time zone (builder's call).
- Scores: the number is always shown, beside a 3-segment bar: high = 3 segments in mist, medium = 2 in mist-2, low = 1 in the border tint. Thresholds are the builder's call, set in one place.
- Kinds: an outlined pill with the word and an icon whose shape differs per kind (hackathon = bolt, internship = graduation cap, job = briefcase, competition = trophy, ideathon = light bulb, university notice = bell, job fair = people, seminar = presentation, other = dot; the builder confirms the list against the engine's kinds). Never colour-coded.
- Status: online = solid mist-2 dot with "checked 21:42"; offline = hollow dot with "Offline since 21:40 · retrying"; failures = `alert` with a warning icon and a fix ("Gmail sign-in expired · run `assistant set-key gmail`").
- Buttons: primary = solid mist fill with ink text; secondary = outline in the border tint; both get the amber edge and glow on hover.

**Type.** A serif for the greeting and the big numbers (the owner chose it, like a notebook): Georgia, already on every Windows PC. Everything else in Segoe UI (Windows' own). Tabular numbers in lists.

**Mockups** shown to the owner live in `.superpowers/brainstorm/` (git-ignored); the screens to match are the Today and Opportunities mockups of direction A. Mobbin references per view: Today [Rox](https://mobbin.com/screens/ac3c5128-08d9-485f-b264-c034728c61c8), Opportunities [Linear](https://mobbin.com/screens/e142df2a-3527-499c-8f81-1b715947ac0c), Emails [Notion Mail](https://mobbin.com/screens/17094e0f-9711-4685-8250-74f1c62164bd), Reports [Wise](https://mobbin.com/screens/3054ca6a-0f2b-4bcc-bc6f-bef7b9132a13), Settings [Railway](https://mobbin.com/screens/2b826de5-1789-405b-98a2-196e4e3aedb2), Ctrl+K [Rise](https://mobbin.com/screens/1e6d5982-0315-4629-9110-8bd4d3a15a44). We borrow patterns, never a product's look.

## How it runs in M3

- The dashboard talks to `assistant engine --dev`. It prints the launch password and lets Vite's origin (`http://localhost:5173`) call the API; the Vite dev server must use exactly that port (strict). One engine runs per database (`engine.lock`), so during M3 the owner's daily engine itself runs in dev mode, from the M3 branch's code until M3 merges (as for M2's live check). It does its normal work (mail checks, extraction) and also serves the dashboard. The builder may start it in a terminal tab and read the password from its output; each restart makes the old password useless.
- First visit: a small screen asks for the launch password and tests it against `GET /api/settings`. It is kept in `sessionStorage` (gone when the tab closes, and different at each engine start anyway). A 401 later returns to that screen.
- The API base is `http://127.0.0.1:47615` (from `[api] port`; the dev setup may let it be overridden).
- If the engine isn't reachable: a full-page state saying so, with the command to start it, retrying every few seconds.

## Data and live updates

- Every call goes through one typed client generated from `app/src/api/schema.d.ts` (`npm run gen:api`), with the password in the `Authorization` header. Server state lives in one query cache.
- The event stream (`GET /api/events`) is read with `fetch` streaming, never `EventSource`, so the password stays in a header. On `item_changed` and `ranking_changed`, refresh the affected opportunities and Today; on `items_new`, refresh lists and counts; on `status`, update the status line. Reconnect with a short backoff when the stream ends.
- Actions (star, unstar, dismiss, restore, applied) update the screen at once and roll back with an `alert` message if the call fails.
- `GET /api/events` has no typed body (FastAPI describes it in OpenAPI 3.2 form, which the generator doesn't read); the four event kinds are typed by hand from `src/assistant/engine/events.py`.
- **Tab counts:** `GET /api/opportunities` returns `tab_total` for the tab asked for, so the counts come from one light request per tab (`limit=1`). No engine change.
- **Engine changes in M3** (each with a Python test, and the contract and types regenerated):
  1. **"New today" means open items first seen today** (Ho Chi Minh City day): not dismissed, applied or expired. `GET /api/opportunities` gains a `new_today` filter with exactly that meaning, and `/api/today`'s `new_today` count uses the same condition, so the card's number matches the list it opens. (Builder's call: an item the owner already dismissed isn't "new".)
  2. **`GET /api/settings` returns the path to `profile.toml`**, for the copy button.
- The number cards open: New today → All tab with `new_today`; Deadlines this week → All tab with `deadline_within=6`; Starred → Starred tab; Applied → Applied tab.

## Views

As spec section 6 describes them, with these M3 specifics:
- **Today:** greeting by time of day (serif), one summary line, the four number cards (each opens its filtered list), "For you today" (top picks with inline actions, plus emails that need action), "Upcoming deadlines", the status line.
- **Opportunities:** tabs with counts; filter chips (kind, deadline window, minimum score, text filter); the "N hidden by filters · Clear" line; dense rows with the one-line why; inline Dismiss and Applied on hover; the details panel (score breakdown, eligibility, prize, Open link, Open email in Gmail per sighting).
- **Important emails:** Important and Other sections, grouped by day, dense rows; the reading pane; a "showing the newest 500" note when the API says `truncated`.
- **Reports:** spend per day with the month's total against the cap, past digests (empty until M6, with a one-line empty state), items per kind and source per week.
- **Settings:** connection rows for Gmail, Anthropic and Telegram (status in words and icons; fixes as copyable commands, since a browser can't run them); caps and `for_you_min_score` **read-only**, each with the `config.toml` key that changes it and a note that the engine needs a restart (builder's call: editing them from the browser would need the engine to rewrite and reload its config); the profile summary; and **Rescore** (calls the API). "Open profile.toml" becomes the file's path with a copy button until M4.
- **Ctrl+K and keys:** as spec section 6. Shortcuts never fire while typing in a field.

## Testing

- Unit and component tests (Vitest and Testing Library) for: relative deadlines in HCMC time around midnight, score segments, the accent rules (no amber outside its allowed places), the keyboard map, the event-stream reader (split chunks, reconnects), optimistic actions and their rollback, 401 handling.
- `npm run check:api` stays green; a TypeScript build with no errors is part of every task's done.
- Python tests for any engine change (for example a counts route), as in M2.
- A look check on the real engine in the browser pane, against the mockups, at the end of the views.
- Tests never call the real engine or the network.

## Left to the builder

Exact package versions (checked against current docs when planning), the router, file layout, list virtualization if long lists need it, score thresholds, animation timings, the empty-state placeholder.
