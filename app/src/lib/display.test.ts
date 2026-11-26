import { expect, test } from "vitest"
import { KINDS, kindOf } from "./kinds"
import { segments } from "./score"
import { describeProblem } from "./status"

test("score segments: 75 → 3, 74 → 2, 50 → 2, 49 → 1", () => {
  expect([segments(100), segments(75), segments(74), segments(50), segments(49), segments(-5)]).toEqual([3, 3, 2, 2, 1, 1])
})

// The engine's kinds, from src/assistant/models.py OPPORTUNITY_KINDS
const ENGINE_KINDS = ["hackathon", "ideathon", "competition", "internship", "job", "job_fair", "seminar", "uni_notice", "other"]

test("every engine kind has a label and its own icon", () => {
  expect(Object.keys(KINDS).sort()).toEqual([...ENGINE_KINDS].sort())
  const icons = Object.values(KINDS).map((k) => k.Icon)
  expect(new Set(icons).size).toBe(icons.length)
  expect(KINDS.uni_notice.label).toBe("University notice")
  expect(KINDS.job_fair.label).toBe("Job fair")
})

test("an unknown kind is shown as other", () => {
  expect(kindOf("workshop")).toBe("other")
  expect(kindOf("seminar")).toBe("seminar")
})

test("status words: auth_lost is an alert with a fix; offline is quiet with the time", () => {
  expect(describeProblem("gmail", "auth_lost", {})).toEqual(
    { text: "Gmail sign-in expired", fix: "assistant set-key gmail", severity: "alert" })
  expect(describeProblem("gmail", "offline", { last_ok_check_at: "2026-11-17T14:40:00+00:00" })).toEqual(
    { text: "Offline since 21:40 · retrying", severity: "quiet" })
})

test("status words for the other sources", () => {
  expect(describeProblem("anthropic", "cap_daily", {}).text).toBe("Today's AI budget is used up · resumes at midnight")
  expect(describeProblem("anthropic", "busy", {}).severity).toBe("quiet")
  expect(describeProblem("anthropic", "anthropic", {})).toEqual(
    { text: "Anthropic refused the request", fix: "assistant set-key anthropic", severity: "alert" })
  expect(describeProblem("profile", "profile", { detail: "line 3: bad key" }))
    .toEqual({ text: "profile.toml has a mistake: line 3: bad key", severity: "alert" })
  expect(describeProblem("job:extract", "job_error", { detail: "extract: KeyError" }))
    .toEqual({ text: "A background job failed (extract: KeyError)", severity: "alert" })
  expect(describeProblem("backup", "backup_failed", { detail: "OSError" }))
    .toEqual({ text: "Last night's backup failed: OSError", severity: "alert" })
  expect(describeProblem("backup", "backup_failed", {}).text).toBe("Last night's backup failed")
  expect(describeProblem("gmail", "something_new", {})).toEqual({ text: "gmail: something_new", severity: "quiet" })
})
