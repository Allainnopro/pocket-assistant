import { afterEach, describe, expect, test, vi } from "vitest"
import { clock, dayGroup, deadlineInfo, greeting, hcmcDate } from "./time"

const at = (iso: string) => new Date(iso)

describe("deadlines, in Ho Chi Minh City time", () => {
  test("a deadline later today shows hours left and is urgent", () => {
    // 04:00 on the 18th in HCMC; the deadline ends at 23:59 that day
    expect(deadlineInfo("2026-11-18", at("2026-11-17T21:00:00Z"))).toEqual({ label: "in 20h", tone: "urgent" })
  })

  test("under an hour shows minutes", () => {
    expect(deadlineInfo("2026-11-18", at("2026-11-18T16:20:00Z"))).toEqual({ label: "in 40m", tone: "urgent" })
  })

  test("HCMC midnight decides what 'today' is", () => {
    expect(hcmcDate(at("2026-11-17T16:59:59Z"))).toBe("2026-11-17")
    expect(hcmcDate(at("2026-11-17T17:00:00Z"))).toBe("2026-11-18")
  })

  test("tomorrow is urgent only inside 48 hours", () => {
    expect(deadlineInfo("2026-11-19", at("2026-11-17T17:30:00Z"))).toEqual({ label: "tomorrow", tone: "urgent" }) // 47.5 h left
    expect(deadlineInfo("2026-11-19", at("2026-11-16T10:00:00Z"))).toEqual({ label: "in 3 days", tone: "week" })
  })

  test("past deadlines are overdue, far ones are later, none is none", () => {
    expect(deadlineInfo("2026-11-10", at("2026-11-17T03:00:00Z"))).toEqual({ label: "overdue", tone: "overdue" })
    expect(deadlineInfo("2026-11-24", at("2026-11-17T03:00:00Z"))).toEqual({ label: "in 7 days", tone: "later" })
    expect(deadlineInfo("2026-11-23", at("2026-11-17T03:00:00Z"))).toEqual({ label: "in 6 days", tone: "week" })
    expect(deadlineInfo(null, at("2026-11-17T03:00:00Z"))).toEqual({ label: "no deadline", tone: "none" })
  })
})

describe("greetings, day groups and clock times", () => {
  test("the greeting follows the HCMC hour", () => {
    expect(greeting(at("2026-11-17T00:30:00Z"))).toBe("Good morning") // 07:30
    expect(greeting(at("2026-11-17T06:00:00Z"))).toBe("Good afternoon") // 13:00
    expect(greeting(at("2026-11-17T13:00:00Z"))).toBe("Good evening") // 20:00
    expect(greeting(at("2026-11-17T19:00:00Z"))).toBe("Good evening") // 02:00
  })

  test("emails group by HCMC day", () => {
    const now = at("2026-11-17T03:00:00Z") // Tue 17 Nov, 10:00
    expect(dayGroup("2026-11-16T18:00:00+00:00", now)).toBe("Today") // 01:00 on the 17th in HCMC
    expect(dayGroup("2026-11-16T08:00:00+00:00", now)).toBe("Yesterday")
    expect(dayGroup("2026-11-14T08:00:00+00:00", now)).toBe("Sat 14 Nov")
  })

  test("clock times are HCMC times", () => {
    expect(clock("2026-11-17T14:42:00+00:00")).toBe("21:42")
  })
})

describe("the browser's own time zone", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  test("the browser's time zone doesn't change any label", () => {
    // Review Focus 5: a laptop set to another time zone, or travelling
    for (const tz of ["Europe/Berlin", "America/Los_Angeles", "UTC"]) {
      vi.stubEnv("TZ", tz)
      expect(deadlineInfo("2026-11-18", at("2026-11-17T21:00:00Z"))).toEqual({ label: "in 20h", tone: "urgent" })
      expect(hcmcDate(at("2026-11-17T17:00:00Z"))).toBe("2026-11-18")
      expect(greeting(at("2026-11-17T13:00:00Z"))).toBe("Good evening")
      expect(dayGroup("2026-11-16T18:00:00+00:00", at("2026-11-17T03:00:00Z"))).toBe("Today")
      expect(clock("2026-11-17T14:42:00+00:00")).toBe("21:42")
    }
  })
})
