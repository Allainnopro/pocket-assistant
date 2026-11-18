import { expect, test } from "vitest"
import { fillDays } from "./series"

const day = (d: string, cost: number) => ({ day: d, cost_usd: cost, calls: 1, input_tokens: 100, output_tokens: 20 })

test("fillDays fills quiet days with zeros and keeps n days", () => {
  const filled = fillDays([day("2026-11-02", 0.25), day("2026-11-17", 0.5)], "2026-11-17", 30)
  expect(filled).toHaveLength(30)
  expect(filled[0].day).toBe("2026-10-19")
  expect(filled.at(-1)).toEqual(day("2026-11-17", 0.5))
  expect(filled.find((d) => d.day === "2026-11-02")?.cost_usd).toBe(0.25)
  expect(filled.find((d) => d.day === "2026-11-03")).toEqual({ day: "2026-11-03", cost_usd: 0, calls: 0, input_tokens: 0, output_tokens: 0 })
})

test("fillDays crosses months and years", () => {
  const filled = fillDays([], "2027-01-02", 4)
  expect(filled.map((d) => d.day)).toEqual(["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"])
})
