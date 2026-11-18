import { cn } from "@/lib/utils"
import { segments } from "@/lib/score"

const LIT = { 3: "bg-mist", 2: "bg-mist-2", 1: "bg-line" } as const
const LEVEL = { 3: "high", 2: "medium", 1: "low" } as const

export function ScoreBar({ score, big = false }: { score: number; big?: boolean }) {
  const lit = segments(score)
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5" title={`Score ${score} (${LEVEL[lit]})`}>
      <span className={cn("tabular-nums text-mist", big ? "font-serif text-3xl" : "w-6 text-sm font-semibold")}>{score}</span>
      <span className="flex gap-0.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <i key={i} data-segment={i < lit ? "on" : "off"}
             className={cn("block w-1 rounded-[1px]", big ? "h-4" : "h-3", i < lit ? LIT[lit] : "bg-line")} />
        ))}
      </span>
    </span>
  )
}
