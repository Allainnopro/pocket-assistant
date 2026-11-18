# M3 dashboard: rulings and deferred minors

Copied from the execution ledger of `2026-11-17-m3-dashboard.md` before its workspace was deleted.

## Rulings

- Task 1: Ruling: api_at moved from test_api_overview.py to tests/api_helpers.py (the plan's second option) — importing it from test_api_overview would make the two test modules import each other (test_api_overview imports seed from test_api_opportunities) — cost if wrong: none
- Task 1: Ruling: utc_text moved to api_deps (shared by api_opportunities and api_overview, which can't import each other that way round); api_overview's now-unused `time` import removed — cost if wrong: none
- Task 2: Ruling: the config files were copied from the research agent's verified scaffold (create-vite's react-ts template output minus oxlint and assets) instead of re-running create-vite — same files, already proven with this exact stack — cost if wrong: none
- Task 2: Ruling: vite.config's test.css.include = /index\.css(\?|$)/ — Vitest returns '' for CSS files even with ?raw unless included; the token test must read the real file — cost if wrong: none
- Task 2: Ruling: the generated dialog overlay `bg-black/10` → `bg-ink/60` — the only non-palette colour shadcn generated (Ctrl+K backdrop) — cost if wrong: none
- Task 2: Ruling: tokens.test also pins the PALETTE object to the owner's exact values and checks --disabled; added `npm run verify` (test + build + check:api), the one command each later task ends with — cost if wrong: none
- Task 3: Ruling: the fake fetch helper lives in src/test/fakeFetch.ts — importing it from client.test.ts would re-register that file's tests inside auth.test — cost if wrong: none
- Task 3: Ruling: the health check doubles as the engine watch: every 3 s while down, every 15 s while up, so a stopped engine shows the "can't reach" page within 15 s — the plan named only the 3 s retry — cost if wrong: one tiny local request every 15 s
- Task 3: Ruling: signIn treats 401/403 as "wrong" and anything else (network error, 5xx, Host refusal) as "unreachable", whose message names the start command — cost if wrong: a misleading hint on an odd failure
- Task 4: Ruling: added useLiveEvents.test.tsx (an item_changed event on a fake stream refreshes ["opportunity", 7] and ["opportunities"]) and the backoff-sequence test beyond the brief — the wiring is what makes "live" true — cost if wrong: none
- Task 4: Ruling: the auth context carries the injected fetch so the event stream uses the same fake in tests (never the real network) — cost if wrong: none
- Task 5: Ruling: hover/focus are two Tailwind utilities (lamp-hover, lamp-focus) defined once in index.css and put on the shared Button's base classes (replacing shadcn's 50% ring); the lamp allowlist test lets those utility names appear anywhere and restricts every other lamp class to Deadline, TopPick, Shell, Toast and tokens.ts — cost if wrong: none
- Task 5: Ruling: added DOM.Iterable to tsconfig.app.json's lib (iterating NodeLists in tests) and the TZ test stubs TZ with vi.stubEnv instead of process.env, so Node's types stay out of the app config — cost if wrong: none
- Task 5: Ruling: extra tests beyond the brief: minutes under an hour, overdue in bold mist (not amber), the 6-vs-7-day week edge, the problem-word table for every source, the alert-before-quiet order in StatusLine, KindPill without colour, buttons carrying the lamp utilities — cost if wrong: none
- Task 6: Ruling: added `g s` → Settings to the spec's g t/o/e/r, and the slash key opens the palette (the spec's "/ search") — cost if wrong: none
- Task 6: Ruling: the sidebar's Emails count reuses the Emails view's default query (Important, 7 days), so it costs no extra request — cost if wrong: the count reads "in the last 7 days" implicitly
- Task 6: Ruling: a shared test harness src/test/renderApp.tsx (memory router + providers + fake engine) for the view tests; each view's own shortcuts register their own listener, so the global and view maps run side by side — cost if wrong: a view key could fire right after a stray "g" (rare)
- Task 7: Ruling: the row lives in components/OpportunityItem.tsx (not in the plan's file list) so Today (Task 8) reuses it; no separate useAct.test.tsx — the page tests pin optimistic change, rollback, both toasts and the a/s/d keys — cost if wrong: none
- Task 7: Ruling: a "New today" chip (removable) appears when ?new=1 is set by the Today card; "Nothing matches these filters." when filters empty a non-empty tab — cost if wrong: none
- Task 8: Ruling: the summary line says "All caught up." when nothing waits (the empty state below carries "Nothing needs you today."), so the two never repeat — cost if wrong: none
- Task 8: Ruling: a stored last_check_error (e.g. auth_lost after an engine restart, when the bus holds no problem yet) is shown as Gmail's problem — cost if wrong: none
- Task 9: Ruling: badges live in components/EmailBadges.tsx (list and pane share them); "addressed the assistant" uses the alert colour on its outline and text, with a warning icon — it marks a prompt-injection attempt, a hard signal — cost if wrong: none
- Task 10: Ruling: no chart tooltip (Recharts' default tooltip is a white box outside the palette); the day numbers on the axis and the words above carry the values — cost if wrong: the owner can't hover a bar for its exact amount
- Task 10: Ruling: dateLabel (series.ts) formats date-only strings in UTC so "2026-11-02" stays Mon 2 Nov whatever the browser zone; digests' send errors are quiet (mist-2), not alert — nothing to fix from here — cost if wrong: none
- Task 11: Ruling: a missing Anthropic key is an alert (with `assistant set-key anthropic`), like an expired Gmail sign-in — extraction can't run without it — cost if wrong: none
- Task 11: Ruling: Rescore also refreshes the opportunity lists, tab counts and Today (the engine's ranking_changed event does too; this makes it immediate) — cost if wrong: none
- Task 12: Ruling: Reports shows September as "Sept" (Intl en-GB's own short month) — left as is — cost if wrong: cosmetic
- Ruling: the engine hands its password to the dev dashboard through a git-ignored file next to app/ — anyone who can read the checkout could already read the engine's terminal and DB — cost if wrong: a local process reads a password that dies with the engine
- Final: Ruling: the filter keeps a guard so its own URL write can't overwrite keys typed while the router's transition is pending — no test can hold a router transition open in jsdom (the mutant removing it survives) — cost if wrong: two harmless lines

## Deferred minors

- Reports on a fresh database shows "Week" headers over 8 empty rows instead of one line
- Ctrl+K palette lets focus leak behind it
- list selection is index-based, so after a live refresh j/k can land on a different item
- ?within=6 (not in the menu) shows the Deadline select as "any"
- needs-action emails on Today show only a time, not a day, when older than today
- the Emails list isn't refreshed by status events
- Chrome's save-password prompt on the login form (mostly moot with automatic sign-in)
- no note when Opportunities hits the 1000-row cap

## Task and review record

- Task 1: complete (commits 5147f60..ea7a8ba, tests: ../../.venv/Scripts/python -m pytest -q -p no:cacheprovider → 926 passed in 38.22s)
- Task 2: complete (commits ea7a8ba..2b6d1c0, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 3: complete (commits 2b6d1c0..38eaffa, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 4: complete (commits 38eaffa..56b9d93, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 5: complete (commits 56b9d93..9be2163, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 6: complete (commits 9be2163..95009b9, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 7: complete (commits 95009b9..46f6ae9, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 8: complete (commits 46f6ae9..62c7e39, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 9: complete (commits 62c7e39..40611e5, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 10: complete (commits 40611e5..593114a, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 11: complete (commits 593114a..83305a0, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 12: complete (commits 83305a0..395564f, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Final: fixed "g s" stars the row instead of opening Settings (each useShortcuts had its own "g" state; the view's listener ran first) — one shared sequence answering exactly one following key; tests "g s goes to Settings even when the view, which registered first, stars with s", "…registered after the Shell (after navigating)", timeout test extended — RED→GREEN, mutants 5/6 killed (the 6th, resetting on mount, is equivalent), suite 118/118 — 9d1fd05
- Final: fixed Enter on a focused link or button opened the selected row instead — Enter on a, button, [role=button], [role=link] is left to that element; test "Enter on a focused link or button belongs to it" RED→GREEN, suite 118/118 — 9d1fd05
- Final: fixed a failed load left a blank page or list, and a >100-character pasted filter blanked the list with a 422 — LoadError ("Couldn't load this from the engine." + Try again) on Today, Settings, Opportunities, Emails, only when nothing has loaded (a failed refresh keeps the list); filter maxLength 100; 6 tests RED→GREEN, mutants 6/6 killed, suite 124/124 — 831c2f3
- Final: fixed the text filter wrote stale text back into the URL after Ctrl+K, a sidebar click or Back — the box follows q when q changes elsewhere; tests "a jump elsewhere … clears the text filter…", "Back to a filter typed in this box…" RED→GREEN, mutants 3/4 killed, suite 126/126 — 5689f5b
- Final: fixed j/k moved the highlight off screen — scrollIntoView({block:"nearest"}) on selection change in Opportunities and Emails; 2 tests RED→GREEN, suite 128/128
