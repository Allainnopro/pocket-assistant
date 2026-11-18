import type { components } from "@/api/schema"

export type EmailKind = components["schemas"]["EmailRow"]["kind"]

/** Each kind's name and dot colour, in the order the legend shows them (design: 2026-11-18-email-kinds). */
export const KIND_INFO: Record<EmailKind, { label: string; colour: string }> = {
  study: { label: "Subjects & program", colour: "var(--kind-study)" },
  uni_admin: { label: "Uni admin", colour: "var(--kind-uni-admin)" },
  hackathon: { label: "Hackathons & contests", colour: "var(--kind-hackathon)" },
  jobs: { label: "Jobs & internships", colour: "var(--kind-jobs)" },
  scholarship: { label: "Scholarships", colour: "var(--kind-scholarship)" },
  news_events: { label: "Uni news & events", colour: "var(--kind-news-events)" },
  uni_mass: { label: "Uni mass mail", colour: "var(--kind-uni-mass)" },
  social: { label: "Social", colour: "var(--kind-social)" },
  promo: { label: "Newsletters & promos", colour: "var(--kind-promo)" },
  accounts: { label: "Accounts & system", colour: "var(--kind-accounts)" },
  unsorted: { label: "Unsorted", colour: "var(--mist)" },
}

export const KIND_ORDER = Object.keys(KIND_INFO) as EmailKind[]
