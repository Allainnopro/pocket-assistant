import { Reply, Sparkles, TriangleAlert, type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

type Badge = "opportunity" | "needs_action" | "addressed_ai"

const BADGES: Record<Badge, { label: string; Icon: LucideIcon; alert?: boolean }> = {
  opportunity: { label: "Opportunity", Icon: Sparkles },
  needs_action: { label: "Needs action", Icon: Reply },
  addressed_ai: { label: "Addressed the assistant", Icon: TriangleAlert, alert: true }, // a prompt-injection attempt
}

export function EmailBadges({ badges }: { badges: Badge[] }) {
  return (
    <span className="flex shrink-0 flex-wrap gap-1">
      {badges.map((badge) => {
        const { label, Icon, alert } = BADGES[badge]
        return (
          <span key={badge} data-badge={badge}
                className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-px text-[11px]",
                  alert ? "border-alert text-alert" : "border-line text-mist-2")}>
            <Icon className="size-3" aria-hidden />{label}
          </span>
        )
      })}
    </span>
  )
}
