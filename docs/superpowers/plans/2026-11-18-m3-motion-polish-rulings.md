# M3 motion and polish: rulings and deferred minors

Copied from the execution ledger of `2026-11-18-m3-motion-polish.md` before its workspace was deleted.

## Rulings

- Task 2: Ruling: "a refresh keeps the rows that stay where they are" passed before the change — it is a Review Focus guard pinning behaviour the change must keep, not a RED test — cost if wrong: none (the arrival tests were RED)
- Task 2: Ruling: Today's footer arrives at 7 + ROW_CAP (15), not 7 as planned, and the "N more" links at the last row level — at 7 the footer would beat the rows (up to 14) and break "top to bottom" — cost if wrong: footer appears 400 ms later
- Task 2: Ruling: the Emails list is keyed by section too (Important/Other replay the cascade), like Opportunities' tabs — same reason the design gives for Opportunities — cost if wrong: one key
- Task 2: Ruling: added test "switching between For you, All and the other tabs replays the arrival" (both tabs cached) after the tab-key mutant survived the plan's tests — cost if wrong: none
- Task 3: Ruling: the marker moves with transform: translateY() rather than the translate property — jsdom drops translate, so the test couldn't see it; browsers treat both the same — cost if wrong: none
- Task 5: Ruling: the overflow fix has no automated test — jsdom has no layout, so it was measured live instead — cost if wrong: a scrollbar flash during arrival
- Final: Ruling: rows that lift on hover rise from below instead of sliding in from the right (the design's direction was my call) — a row sliding in under the pointer is also 2 % wider, together more than the padding; a list-only clip failed (min-w-fit grew to 2909 px on untruncated titles) — cost if wrong: rows cascade down rather than across
- Final: Ruling: panels replay their slide-in on every newly opened item — the plan asks for it — cost if wrong: feels busy with fast Enter
- Final: Ruling: the Ctrl+K palette's own fade and zoom run under reduced motion — predates this change (shadcn dialog), out of scope — cost if wrong: a short zoom for reduced-motion users
- Final: Ruling: hundreds of rows animating at once was not frame-measured — opacity and translate only, on the compositor; live runs looked smooth — cost if wrong: a stutter on a slow machine
- Final: Ruling: the count-up eases from the shown number when the value changes mid-count rather than jumping — it still ends on the real value — cost if wrong: a 600 ms count on live changes
- Final: Ruling: button and nav-colour fades still run under reduced motion — fades, not movement — cost if wrong: minimal
- Final: Ruling: lift and the sidebar underline aren't limited to (hover: hover) — the dashboard is a desktop app — cost if wrong: a tapped row stays lifted on touch
- Final: Ruling: a live row deep in a list waits up to 550 ms before arriving; a refetch moving a row to a later level within the first 0.9 s could hide it briefly — consistent with the spec, rare — cost if wrong: a brief flicker

## Deferred minors

- every filter change (text, kind, deadline, score; Emails' days) blanks the list and replays the cascade (no placeholderData)
- a new email at the top of a day makes that day's heading re-arrive
- under reduced motion, hovering the j/k-selected row hides its selection edge (unlayered box-shadow: none)
- Settings' sections overlap their waves by 50–100 ms (levels restart per section)
- the Emails "newest 500" note doesn't arrive with the rest
- the refresh guard test refetches identical data, so it can't fail; should refetch with a changed row
- no test for useCountUp when the value changes mid-count (works today, checked by probe)
- Today's arrival test doesn't cover email/deadline links and the footer
- ROW_CAP puts rows 9+ one beat after the 8th, not with it (spec wording vs plan numbers)
- the 2 px sidebar marker is square against the link's rounded corners

## Task and review record

- Task 1: complete (commits 595f220..9524cec, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 2: complete (commits 9524cec..7e68281, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 3: complete (commits 7e68281..74e6193, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 4: complete (commits 74e6193..52b555b, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 5: look check (built-in browser, 1280x800, real data): Today cascade caught mid-arrival (greeting in, first card sliding, rest waiting) and settled; count-up ended on real numbers; row hover measured scale 1.02, margin 6px/6px, hover background; sidebar 8px gaps, amber underline on hover, marker on the current page; Opportunities j + Enter opened the panel (panel-in, settled at opacity 1); Settings plain list with bold headings; Emails list settled; a fresh reload through all five pages logged no errors (older useRef errors in the console were from an HMR moment during the earlier filter fix)
- Task 5: fixed: Opportunities/Emails overflowed sideways by up to 12px during the wind-up (padding 24px < 26px pull) — overflow-x-hidden on every page's scroll area; re-measured: overflow clipped, no scrollbar
- Task 5: complete (commits 52b555b..5ac9459, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Final review: workflow wf_ae94f9fc-519 (3 lenses on opus + 1 skeptic): 11 confirmed (1 important, 10 minor), 1 rejected
- Final: re-graded "the sideways clip hides real overflow at narrow widths" minor → important — a row's deadline and Dismiss/Applied become unreachable by mouse with a panel open below ~1090 px
- Final: fixed lifted rows shake under a still pointer near the top edge (reproduced live: 25 hover flips in 40 frames) — 6 px ::before hover zone, off under reduced motion; test "a lifted row keeps its hover area while it makes room…" RED→GREEN, live 0 flips, suite 141/141
- Final: fixed the page-wide overflow-x-hidden clipping real content — arrive pull 26 → 20 px (inside the 24 px padding) and lifting rows use a vertical "rise" keyframe, clips removed; tests "the wind-up stays inside the narrowest page padding…" and "rows that grow on hover rise into place…" RED→GREEN, live 0 px sideways overflow with a grown row under the pointer on every page, suite 141/141
