import { expect, test } from "vitest"

// The look allows five colours plus the alert red, all defined once in index.css and tokens.ts.
// Our own code must never write a raw colour or reach for Tailwind's default palette.
const files = import.meta.glob<string>(
  ["./**/*.{ts,tsx}", "!./components/ui/**", "!./tokens.ts", "!./**/*.test.*", "!./api/schema.d.ts"],
  { query: "?raw", import: "default", eager: true },
)
const STRAY =
  /#[0-9a-f]{3,8}\b|\b(?:bg|text|border|ring|fill|stroke|shadow|outline|from|to|via)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone|black|white)(?:-\d{2,3})?\b|\brgba?\(|\bhsla?\(|\boklch\(/gi

test("the guard sees our files", () => {
  expect(Object.keys(files)).toContain("./main.tsx")
})

test("no raw colours or Tailwind palette colours outside the tokens", () => {
  for (const [path, text] of Object.entries(files)) expect([path, text.match(STRAY)]).toEqual([path, null])
})
