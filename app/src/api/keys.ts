// Query keys. Each starts with its resource name, so invalidating ["opportunities"] refreshes every list.
export const keys = {
  health: ["health"] as const,
  today: ["today"] as const,
  opportunities: (params: object) => ["opportunities", params] as const,
  opportunity: (id: number) => ["opportunity", id] as const,
  tabCount: (tab: string) => ["tabCount", tab] as const,
  emails: (params: object) => ["emails", params] as const,
  email: (id: string) => ["email", id] as const,
  search: (q: string) => ["search", q] as const,
  spend: (days: number) => ["spend", days] as const,
  digests: ["digests"] as const,
  items: (weeks: number) => ["items", weeks] as const,
  settings: ["settings"] as const,
  profileText: ["profileText"] as const,
}
