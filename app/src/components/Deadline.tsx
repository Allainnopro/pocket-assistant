import { Clock } from "lucide-react"
import { cn } from "@/lib/utils"
import { deadlineInfo, type Tone } from "@/lib/time"

const TONE: Record<Tone, string> = {
  urgent: "text-lamp", // inside 48 hours: one of amber's few jobs
  overdue: "text-mist font-semibold",
  week: "text-mist",
  later: "text-mist-2",
  none: "text-mist-2",
}

export function Deadline({ deadline, now, className }: { deadline: string | null; now: Date; className?: string }) {
  const { label, tone } = deadlineInfo(deadline, now)
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-xs tabular-nums", TONE[tone], className)}>
      {tone === "urgent" && <Clock className="size-3" aria-hidden />}
      {label}
    </span>
  )
}
