import { clock } from "./time"

export type Problem = { text: string; fix?: string; severity: "alert" | "quiet" }

// The engine reports one problem per source (engine/jobs.py, EventBus.report). "alert" means the owner
// must do something (soft red, icon, words, the fix); "quiet" means the engine is already retrying.
export function describeProblem(source: string, problem: string, details: Record<string, unknown>): Problem {
  const detail = typeof details.detail === "string" ? details.detail : ""
  if (source === "gmail") {
    switch (problem) {
      case "auth_lost":
        return { text: "Gmail sign-in expired", fix: "on the VM, as pocket: assistant set-key gmail (see deploy/README.md)", severity: "alert" }
      case "offline": {
        const since = typeof details.last_ok_check_at === "string" ? ` since ${clock(details.last_ok_check_at)}` : ""
        return { text: `Offline${since} · retrying`, severity: "quiet" }
      }
      case "gmail_busy":
        return { text: "Gmail is limiting checks · retrying", severity: "quiet" }
      case "untrusted_network":
        return { text: "Gmail's certificate didn't check out on this network", severity: "alert" }
    }
  }
  if (source === "anthropic") {
    switch (problem) {
      case "busy":
      case "no_answer":
      case "offline":
        return { text: "Anthropic is busy · retrying", severity: "quiet" }
      case "cap_daily":
        return { text: "Today's AI budget is used up · resumes at midnight", severity: "quiet" }
      case "anthropic":
        return { text: "Anthropic refused the request", fix: "on the VM, as pocket: assistant set-key anthropic (see deploy/README.md)", severity: "alert" }
    }
  }
  if (source === "profile" && problem === "profile") {
    return { text: detail ? `profile.toml has a mistake: ${detail}` : "profile.toml has a mistake", severity: "alert" }
  }
  if (source === "backup" && problem === "backup_failed") {
    return { text: detail ? `Last night's backup failed: ${detail}` : "Last night's backup failed", severity: "alert" }
  }
  if (source.startsWith("job:") && problem === "job_error") {
    return { text: `A background job failed (${detail || source.slice(4)})`, severity: "alert" }
  }
  return { text: `${source}: ${problem}`, severity: "quiet" }
}
