import { Bell, Briefcase, Dot, GraduationCap, Lightbulb, Presentation, Trophy, Users, Zap, type LucideIcon } from "lucide-react"

// The engine's kinds (src/assistant/models.py OPPORTUNITY_KINDS). Told apart by icon shape, never by colour.
export type Kind = "hackathon" | "ideathon" | "competition" | "internship" | "job" | "job_fair" | "seminar" | "uni_notice" | "other"

export const KINDS: Record<Kind, { label: string; Icon: LucideIcon }> = {
  hackathon: { label: "Hackathon", Icon: Zap },
  ideathon: { label: "Ideathon", Icon: Lightbulb },
  competition: { label: "Competition", Icon: Trophy },
  internship: { label: "Internship", Icon: GraduationCap },
  job: { label: "Job", Icon: Briefcase },
  job_fair: { label: "Job fair", Icon: Users },
  seminar: { label: "Seminar", Icon: Presentation },
  uni_notice: { label: "University notice", Icon: Bell },
  other: { label: "Other", Icon: Dot },
}

export function kindOf(kind: string): Kind {
  return kind in KINDS ? (kind as Kind) : "other"
}
