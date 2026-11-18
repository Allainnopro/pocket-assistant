# M3 Motion and Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staggered wind-up arrivals, grow-and-make-space hover, count-up numbers, a sliding sidebar marker, a plain-list Settings and a roomier sidebar — all off under reduced motion.

**Architecture:** One CSS layer in `app/src/index.css` (keyframes + `@utility` classes + one reduced-motion block) and one small module `app/src/lib/motion.ts` (stagger helpers, reduced-motion check, count-up hook). Views only add classes and a `--i` style; no data or API changes.

**Tech Stack:** React 19, Tailwind 4 (`@utility`), tw-animate-css (installed), Vitest + jsdom.

**Spec:** `docs/superpowers/specs/2026-11-18-m3-motion-polish-design.md`

## Global Constraints

- No new dependency. Palette unchanged; amber only through `lamp-*` utilities or the allowlisted files (Shell, Toast, Deadline, TopPick, tokens).
- Arrival ≈ 350 ms per element with a slight overshoot; 50 ms between levels; in a list only the first 8 rows step, the rest arrive with the 8th.
- Hover on rows: grow ≈ 2 % and ≈ 6 px more space above and below, 180 ms.
- Sidebar: 8 px between tabs; amber underline grows from the left on hover.
- `prefers-reduced-motion: reduce` switches every animation and the hover growth off (hover falls back to shading); the count-up shows the final number at once.
- `animation-fill-mode: backwards` on arrivals, never `both`/`forwards`.
- `npm run verify` (test + build + check:api) green after every task.

## Review Focus

1. A live refresh (status event, refetch) must not replay the cascade for rows that stay → Task 2 test "a refresh keeps the rows that stay where they are".
2. A long list (hundreds of rows) must not crawl in → Task 1 test on `rowStep` caps + Task 2 test on Opportunities rows' `--i`.
3. Reduced motion → Task 1 test reads index.css: the reduce block turns off `arrive`, `panel-in` and the `lift` growth.
4. Hover growth must not be cancelled by a held arrival transform → Task 1 test: the `arrive` utility uses `backwards`.
5. Today's numbers must end on the real value (and show it at once for reduced motion or when the value changes mid-count) → Task 1 tests for `useCountUp`.

---

### Task 1: Motion layer and helpers

**Files:**
- Modify: `app/src/index.css` (append)
- Create: `app/src/lib/motion.ts`, `app/src/lib/motion.test.tsx`

**Interfaces:**
- Produces:
  - CSS utilities `arrive` (wind-up + slide, delay `calc(var(--i, 0) * 50ms)`), `panel-in` (slide from the right), `lift` (row hover: `scale` ≈ 1.02, `margin-block` +6 px, raised panel background, 180 ms), `lamp-underline` (1 px amber underline growing from the left on hover), and keyframes `arrive`, `panel-in`.
  - `export const ROW_CAP = 8`
  - `export function step(i: number): React.CSSProperties` → `{ "--i": i }`
  - `export function rowStep(base: number, row: number): React.CSSProperties` → `step(base + Math.min(row, ROW_CAP))`
  - `export function prefersReducedMotion(): boolean` — true when `matchMedia` is missing (jsdom) or matches `(prefers-reduced-motion: reduce)`.
  - `export function useCountUp(value: number, ms = 600): number` — eases (ease-out cubic) from the last shown value to `value` with `requestAnimationFrame`; returns `value` at once under reduced motion.

- [ ] **Step 1: Write the failing tests** in `app/src/lib/motion.test.tsx`:
  - `rowStep caps after 8 rows`: `rowStep(3, 0)["--i"] === 3`, `rowStep(3, 7)["--i"] === 10`, `rowStep(3, 200)["--i"] === 11`.
  - `the arrival keeps no transform after it ends` (reads `../index.css?raw`): the `@utility arrive` block contains `backwards` and not `forwards` or ` both`.
  - `reduced motion switches the motion off` (index.css): a `@media (prefers-reduced-motion: reduce)` block names `.arrive`, `.panel-in`, `.lift` and sets `animation: none` and removes the growth (`scale: none`, `margin-block: 0`).
  - `the count-up ends on the value`: stub `matchMedia` → `{ matches: false }`, fake timers; render a component showing `useCountUp(19)`; after `vi.advanceTimersByTime(700)` the text is `19`, and partway (200 ms) it is a number strictly between 0 and 19.
  - `under reduced motion the count-up shows the value at once`: `matchMedia` → `{ matches: true }`; first render shows `19`.
- [ ] **Step 2: Run** `npx vitest run src/lib/motion.test.tsx` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `motion.ts` and the CSS. Wind-up keyframe from the right and a little below: `0% {opacity:0; translate: 18px 10px}` → `30% {opacity:.5; translate: 26px 14px}` (the pull back) → `78% {opacity:1; translate: -3px -1px}` (overshoot) → `100% {translate: 0 0}`, 360 ms. Use the individual `translate`/`scale` properties so arrival and hover compose.
- [ ] **Step 4: Run** the file, then `npm run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Motion layer: staggered wind-up arrival, lift hover, count-up, reduced-motion off switch`.

### Task 2: Pages arrive and rows lift

**Files:**
- Modify: `app/src/views/{Today,Opportunities,Emails,Reports,OpportunityPanel,EmailPane}.tsx`, `app/src/components/{OpportunityItem,Toast}.tsx`
- Test: `app/src/views/{Today,Opportunities}.test.tsx`

**Interfaces:** Consumes `arrive`, `panel-in`, `lift`, `step`, `rowStep`, `useCountUp` from Task 1.

Levels (`--i`): Today — header 0, cards 1–4 (left to right), the two sections 5, their rows `rowStep(6, r)`, footer 7. Opportunities — h1 0, tabs 1, filters 2, rows `rowStep(3, r)`; the list container is keyed by `tab` so an inner tab switch replays the cascade. Emails — h1 0, controls 1, rows and day headings `rowStep(2, r)` by row index. Reports — h1 0, sections 1, 2, 3. Panels — `panel-in` on the `<aside>`, keyed by the open id. Rows (OpportunityItem, Emails rows, Today's email and deadline links) take `lift` in place of `hover:bg-hover`; Today's cards lift without the margin (grid). OpportunityItem's buttons get `transition-opacity`. Toasts: `animate-in fade-in slide-in-from-bottom-2`.

- [ ] **Step 1: Write the failing tests:**
  - Opportunities `rows arrive one by one for the first 8, then together`: with 12 rows, the `--i` of `[data-row]` elements in order is `[3,4,5,6,7,8,9,10,11,11,11,11]`, and each has class `arrive`.
  - Opportunities `a refresh keeps the rows that stay where they are`: grab the `[data-row="1"]` node, `await act(() => queryClient.refetchQueries())` plus one tick, the node in the document is the same object (`toBe`).
  - Today `the page arrives top to bottom`: the h1's `--i` < every card's `--i` < every `[data-row]`'s `--i`, all with class `arrive`.
- [ ] **Step 2: Run** both files — Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as listed above; Today's numbers render `useCountUp(data.counts[key])` in a small `CountUp` component.
- [ ] **Step 4: Run** `npm run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Pages wind up and slide in, rows lift on hover, panels slide in`.

### Task 3: Sidebar spacing, underline and sliding marker

**Files:**
- Modify: `app/src/components/Shell.tsx`
- Test: `app/src/components/Shell.test.tsx` (rewrite the "lamp edge" test)

**Interfaces:** Consumes `lamp-underline` (Task 1).

The nav gets `gap-2`; inactive links lose `hover:bg-hover` and get `lamp-underline` on their label; the active link keeps `bg-hover` but its inset lamp shadow moves to one sliding marker: `<span data-marker aria-hidden>` (2 px amber bar, `transition: translate 320ms` with a slight overshoot, off under reduced motion), positioned by `useLayoutEffect` on the pathname from the `aria-current="page"` link's `offsetTop`/`offsetHeight`.

- [ ] **Step 1: Rewrite the test** `the lamp marker sits on the current page and follows it`: stub `HTMLElement.prototype.offsetTop` to return 40 for the Opportunities link and 80 for the Emails link (by `textContent`); on `/opportunities` the marker's `style.translate` is `0 40px`; after `g e` it is `0 80px`; no NavLink's `className` contains `--lamp` (the marker carries it now).
- [ ] **Step 2: Run** `npx vitest run src/components/Shell.test.tsx` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Sidebar: more room, amber underline on hover, a marker that slides to the current page`.

### Task 4: Settings as a plain list

**Files:**
- Modify: `app/src/views/Settings.tsx`
- Test: `app/src/views/Settings.test.tsx`

Remove the `card` boxes; each section is a `<section>` with an `<h2 className="text-lg font-semibold text-mist">` heading; rows keep their thin top dividers; content `max-w-3xl`; sections `gap-8`; arrival levels: h1 0, then each section heading and its rows step down (`step`).

- [ ] **Step 1: Write the failing test** `Settings is a plain list split by bold headings`: headings level 2 named "Connected accounts", "Limits", "Profile" exist; none of their `<section>` ancestors has class `rounded-xl` or `border` (a box).
- [ ] **Step 2: Run** `npx vitest run src/views/Settings.test.tsx` — Expected: the new test FAILS.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm run verify` — Expected: all pass.
- [ ] **Step 5: Commit** `Settings: a plain list with bold section headings`.

### Task 5: Look check on the real engine

- [ ] **Step 1:** In the built-in browser (Vite dev, engine in dev mode), visit each sidebar page; screenshot mid-arrival and settled; hover a row on Opportunities and Emails; open a panel; confirm j/k still scrolls and selects, Ctrl+K still opens.
- [ ] **Step 2:** Fix what looks wrong (test first for behaviour); ledger each.
- [ ] **Step 3:** `npm run verify` — Expected: all pass.
