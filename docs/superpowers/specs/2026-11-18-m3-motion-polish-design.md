# M3 dashboard: motion and polish (short design)

Approved by the owner on 2026-11-18, after trying the built dashboard. It builds on
`2026-11-17-m3-dashboard-design.md` and changes only the look and motion; the data, routes and API stay as they are.

## Why

The owner likes the Ink & Lamplight look but wants it to impress: the dashboard should feel
alive when you move around it, without losing its calm, night-study feel. Two screens also read poorly:
Settings is boxed when it only needs a list, and the sidebar tabs sit too close together.

## Goals

- **Page arrival.** Switching sidebar tabs makes everything on the new page wind up (a small pull back) and slide in,
  top to bottom: header first, then cards, then rows, each a beat later. The owner asked that components don't arrive
  together, because the delayed arrival of lower components is easy on the eyes.
- **Hover.** An item under the pointer grows a little and makes space around itself, instead of plain shading.
- **Extras (the owner invited creative ideas).** Today's numbers count up; the sidebar's active marker slides between tabs;
  detail panels slide in from the right; rows that arrive live (new mail, new opportunities) arrive the same way.
- **Settings** becomes a plain list: no boxes, sections split by enlarged bold headings.
- **Sidebar:** at least 5 px more between tabs; a thin amber underline on hover.

## Non-goals

- The email colour dots (the second part of this feedback round); they get their own design.
- New pages, data, or API changes.

## Anti-goals (a failure even if it works)

- Motion that slows the owner down: a long list must never crawl in row by row, and nothing waits on an animation
  before it can be clicked or typed into.
- Bounce that turns the calm look cartoonish.
- Motion for someone who asked Windows for less ("Animation effects" off → `prefers-reduced-motion: reduce`): then it
  all switches off, and hover falls back to the old shading (a layout that jumps without animation is worse).

## Constraints

- Palette unchanged; amber (`lamp`) stays where the allowlist test allows it (Shell owns the sidebar underline).
- No new dependency: plain CSS keyframes and transitions, plus a few lines of React for the count-up and the sliding
  marker. `tw-animate-css` is already installed and may be used.
- Everything that exists keeps working: keyboard shortcuts, j/k selection and scroll, the colour and lamp guards.

## Approaches

1. **Plain CSS (chosen; owner handed over the how, approved in the short design).** One `arrive` keyframe with a
   wind-up and a slight overshoot, staggered by a CSS variable `--i` per element; hover with transitions. No
   dependency, runs on the compositor, trivially switched off for reduced motion.
2. **A motion library (Motion / framer-motion).** Springs, layout and exit animations for free, but a new dependency
   of ~30 kB for effects CSS already does here.
3. **The browser's View Transitions on navigation.** Cross-fades whole pages, but animates snapshots, not the
   individual components, so it can't stagger the arrival the owner asked for.

## Decided (from the approved short design)

- Arrival: about 350 ms per element, slight overshoot, 50 ms between levels. In a list, only the first 8 rows arrive
  one by one; the rest arrive with the 8th, so a list of hundreds never crawls in.
- Hover on rows: grows about 2 % and gains about 6 px above and below, over 180 ms.
- Sidebar: 8 px between tabs (was 2 px); the amber underline grows from the left on hover.
- Count-up on Today's four numbers; sliding sidebar marker; panels slide in from the right; live rows arrive like
  the rest.
- Reduced motion turns all of it off.

## Left to the builder (my calls, marked so the owner can check them)

- Direction: elements come in from the right and below a little (a lateral wave suits switching tabs); the wind-up
  dips them further away before they travel in.
- `animation-fill-mode: backwards`, never `both`/`forwards`: a held end transform would override the hover scale.
- Inner tab switches on Opportunities (For you, All, …) replay the cascade for the list, like a page switch.
- Today's cards grow on hover but can't push neighbours in their grid; they lift instead.
- Opportunity row buttons fade in on hover instead of snapping; toasts slide up as they appear.
- Settings keeps thin dividers between rows (a list, not boxes) and a readable max width.
