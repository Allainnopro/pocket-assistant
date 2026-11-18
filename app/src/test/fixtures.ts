import type { components } from "@/api/schema"

export type Row = components["schemas"]["OpportunityRow"]
export type Detail = components["schemas"]["OpportunityDetail"]

/** An opportunity as the API returns it. */
export function row(id: number, more: Partial<Row> = {}): Row {
  return {
    id, kind: "internship", title: `Opportunity ${id}`, organizer: "Example Robotics", deadline: "2026-11-30",
    location: "Thu Duc, HCMC", location_scope: "hcmc", score: 80 - id, why: "An AI internship in the city.", seen_count: 1,
    starred: false, status: "active", url: `https://example.com/${id}`, flagged: false, source: "linkedin",
    first_seen_at: "2026-11-16 03:00:00", ...more,
  }
}

export function detail(id: number, more: Partial<Detail> = {}): Detail {
  return {
    ...row(id), eligibility: "University students", prize_or_pay: "$5,000",
    breakdown: [{ label: "Interests: AI", points: 40 }, { label: "Place: Ho Chi Minh City", points: 25 }],
    explanation: "+40 interests, +25 place",
    sightings: [{ message_id: "m1", seen_at: "2026-11-14T08:00:00+00:00", subject: "Devpost newsletter",
                  sender_name: "Devpost", gmail_url: "https://mail.google.com/mail/#all/m1" }],
    ...more,
  }
}
