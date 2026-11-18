// Every date the owner sees follows Ho Chi Minh City time, whatever the browser's own clock says.
// Never use getHours()/getDate(): they read the browser's time zone.
export const HCMC = "Asia/Ho_Chi_Minh"
const HOUR = 3_600_000
const DAY = 24 * HOUR

const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: HCMC, year: "numeric", month: "2-digit", day: "2-digit" })
const hourOf = new Intl.DateTimeFormat("en-GB", { timeZone: HCMC, hour: "2-digit", hourCycle: "h23" })
const hhmm = new Intl.DateTimeFormat("en-GB", { timeZone: HCMC, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
const dayLabel = new Intl.DateTimeFormat("en-GB", { timeZone: HCMC, weekday: "short", day: "numeric", month: "short" })

export type Tone = "overdue" | "urgent" | "week" | "later" | "none"

/** YYYY-MM-DD: the date in Ho Chi Minh City at that moment. */
export function hcmcDate(moment: Date): string {
  return ymd.format(moment)
}

function dayNumber(date: string): number {
  return Date.parse(`${date}T00:00:00Z`) / DAY
}

/** A deadline is a date; it ends at 23:59 that day in HCMC (UTC+7, no daylight saving). */
export function deadlineInfo(deadline: string | null, now: Date): { label: string; tone: Tone } {
  if (!deadline) return { label: "no deadline", tone: "none" }
  const left = Date.parse(`${deadline}T23:59:59.999+07:00`) - now.getTime()
  if (left < 0) return { label: "overdue", tone: "overdue" }
  const days = dayNumber(deadline) - dayNumber(hcmcDate(now))
  const tone: Tone = left <= 48 * HOUR ? "urgent" : days <= 6 ? "week" : "later"
  if (days === 0) {
    const minutes = Math.ceil(left / 60_000)
    return { label: minutes < 60 ? `in ${minutes}m` : `in ${Math.ceil(left / HOUR)}h`, tone }
  }
  return { label: days === 1 ? "tomorrow" : `in ${days} days`, tone }
}

export function greeting(now: Date): string {
  const hour = Number(hourOf.format(now))
  if (hour >= 5 && hour < 12) return "Good morning"
  if (hour >= 12 && hour < 18) return "Good afternoon"
  return "Good evening"
}

/** "Today", "Yesterday", or e.g. "Sat 14 Nov", by HCMC day. */
export function dayGroup(iso: string, now: Date): string {
  const days = dayNumber(hcmcDate(now)) - dayNumber(hcmcDate(new Date(iso)))
  if (days === 0) return "Today"
  if (days === 1) return "Yesterday"
  return dayLabel.format(new Date(iso)).replace(",", "")
}

/** "21:42", in HCMC. */
export function clock(iso: string): string {
  return hhmm.format(new Date(iso))
}
