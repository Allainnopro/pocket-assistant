import { TriangleAlert } from "lucide-react"
import { describeProblem, type Problem } from "@/lib/status"
import { clock } from "@/lib/time"

type Problems = Record<string, Record<string, unknown>>

/** The most important problem first: something the owner must fix, then something the engine is retrying. */
function worst(problems: Problems): Problem | null {
  const all = Object.entries(problems).map(([source, details]) =>
    describeProblem(source, String(details.problem ?? ""), details))
  return all.find((p) => p.severity === "alert") ?? all[0] ?? null
}

export function StatusLine({ lastCheck, problems, spend }: { lastCheck: string | null; problems: Problems; spend: string }) {
  const problem = worst(problems)
  const tail = spend ? ` · ${spend}` : ""
  if (problem?.severity === "alert") {
    return (
      <p data-severity="alert" className="flex flex-wrap items-center gap-1.5 text-xs text-alert">
        <TriangleAlert className="size-3.5" aria-hidden />
        <span>{problem.text}</span>
        {problem.fix && <>· <code className="rounded bg-hover px-1 text-mist">{problem.fix}</code></>}
        {tail && <span className="text-mist-2">{tail}</span>}
      </p>
    )
  }
  if (problem) {
    return (
      <p data-severity="quiet" className="flex items-center gap-1.5 text-xs text-mist-2">
        <span className="size-1.5 rounded-full border border-mist-2" aria-hidden />
        {problem.text}{tail}
      </p>
    )
  }
  return (
    <p className="flex items-center gap-1.5 text-xs text-mist-2">
      <span className="size-1.5 rounded-full bg-mist-2" aria-hidden />
      {`${lastCheck ? `Checked ${clock(lastCheck)} · ` : ""}Online${tail}`}
    </p>
  )
}
