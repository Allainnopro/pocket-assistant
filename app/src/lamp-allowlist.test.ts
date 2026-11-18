import { expect, test } from "vitest"

// The anti-goal: "If amber starts appearing everywhere, the design has failed." Amber is only for hover and
// focus (the lamp-hover / lamp-focus utilities, allowed anywhere), the top pick, the current page, urgent
// deadlines and the success glow. Any other use of the lamp colour must be added here on purpose.
const ALLOWED = new Set([
  "./tokens.ts",
  "./components/Deadline.tsx",
  "./components/TopPick.tsx",
  "./components/Shell.tsx",
  "./components/Toast.tsx",
])
const files = import.meta.glob<string>(["./**/*.{ts,tsx}", "!./**/*.test.*", "!./api/schema.d.ts"],
  { query: "?raw", import: "default", eager: true })
const LAMP = /\b(?:text|bg|border|ring|shadow|fill|stroke|outline|from|to|via|decoration|caret|accent)-lamp\b|var\(--lamp\b|PALETTE\.(?:lamp|glow)\b/

test("the guard sees our files", () => {
  expect(Object.keys(files)).toContain("./main.tsx")
})

test("lamp classes appear only in the allowed files", () => {
  const using = Object.entries(files).filter(([, text]) => LAMP.test(text)).map(([path]) => path)
  expect(using.filter((path) => !ALLOWED.has(path))).toEqual([])
})
