import type { components } from "@/api/schema"

export type SpendDay = components["schemas"]["SpendDay"]

const DAY = 86_400_000
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10)

/** n consecutive days ending at lastDay (YYYY-MM-DD, HCMC), with zeros where the engine made no calls:
 * the API lists only days that had calls. */
export function fillDays(days: SpendDay[], lastDay: string, n: number): SpendDay[] {
  const byDay = new Map(days.map((d) => [d.day, d]))
  const last = Date.parse(`${lastDay}T00:00:00Z`)
  return Array.from({ length: n }, (_, i) => {
    const day = isoDay(last - (n - 1 - i) * DAY)
    return byDay.get(day) ?? { day, cost_usd: 0, calls: 0, input_tokens: 0, output_tokens: 0 }
  })
}

const label = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" })

/** "Mon 2 Nov" for a date-only string (no time zone shift: the date is already HCMC's). */
export function dateLabel(date: string): string {
  return label.format(new Date(`${date}T00:00:00Z`)).replace(",", "")
}
