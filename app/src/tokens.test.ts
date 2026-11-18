import { expect, test } from "vitest"
import css from "./index.css?raw"
import { KIND_INFO } from "./lib/emailKinds"
import { KIND_COLOURS, PALETTE } from "./tokens"

test("index.css defines each palette colour with its exact value", () => {
  const expected: [string, string][] = [
    ["ink", PALETTE.ink], ["panel", PALETTE.panel], ["mist", PALETTE.mist], ["mist-2", PALETTE.mist2],
    ["lamp", PALETTE.lamp], ["alert", PALETTE.alert], ["line", PALETTE.border], ["hover", PALETTE.hover],
    ["disabled", PALETTE.disabled], ["lamp-glow", PALETTE.glow],
  ]
  for (const [name, hex] of expected) expect(css).toMatch(new RegExp(`--${name}:\\s*${hex};`, "i"))
})

test("the palette is the one the owner chose", () => {
  expect(PALETTE).toEqual({
    ink: "#0F1319", panel: "#181D25", mist: "#E6E9EE", mist2: "#949CA8", lamp: "#E6AE5C", alert: "#E07A6E",
    border: "#333942", hover: "#22272F", disabled: "#5C636D", glow: "#362F25",
  })
})

test("shadcn's own tokens point at the palette", () => {
  expect(css).toMatch(/--background:\s*var\(--ink\)/)
  expect(css).toMatch(/--foreground:\s*var\(--mist\)/)
  expect(css).toMatch(/--primary:\s*var\(--mist\)/)
  expect(css).toMatch(/--primary-foreground:\s*var\(--ink\)/)
  expect(css).toMatch(/--ring:\s*var\(--lamp\)/)
  expect(css).toMatch(/--destructive:\s*var\(--alert\)/)
  expect(css).toMatch(/--border:\s*var\(--line\)/)
})

test("dark only, and the owner's fonts", () => {
  expect(css).not.toMatch(/^\.dark\s*\{/m) // one theme, in :root
  expect(css).toMatch(/color-scheme:\s*dark/)
  expect(css).toMatch(/--font-sans:\s*"Segoe UI"/)
  expect(css).toMatch(/--font-serif:\s*Georgia/)
  expect(css).not.toMatch(/geist/i)
})

test("hover and focus are an amber edge and glow, never amber text", () => {
  expect(css).toMatch(/@utility lamp-hover\s*\{[^}]*&:hover[^{]*\{[^}]*box-shadow:\s*0 0 0 1px var\(--lamp\),\s*0 0 0 4px var\(--lamp-glow\)/)
  expect(css).toMatch(/@utility lamp-focus\s*\{[^}]*&:focus-visible[^{]*\{[^}]*outline:\s*2px solid var\(--lamp\)/)
})

test("every email kind has a name and its colour token", () => {
  const colours = {
    study: "#7DD3FC", uni_admin: "#A78BFA", hackathon: "#FB923C", jobs: "#FACC15", scholarship: "#E879F9",
    news_events: "#4ADE80", uni_mass: "#6B7280", social: "#94A3B8", promo: "#C8A97E", accounts: "#E5E7EB",
  }
  expect(KIND_COLOURS).toEqual(colours)
  for (const [kind, hex] of Object.entries(colours)) expect(css).toMatch(new RegExp(`--kind-${kind.replace("_", "-")}:\\s*${hex};`, "i"))
  expect(Object.keys(KIND_INFO)).toEqual(["study", "uni_admin", "hackathon", "jobs", "scholarship", "news_events",
                                          "uni_mass", "social", "promo", "accounts", "unsorted"])
  expect(KIND_INFO.jobs.label).toBe("Jobs & internships")
})
