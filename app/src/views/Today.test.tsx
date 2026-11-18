import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"
import { row } from "@/test/fixtures"
import { engine, renderApp } from "@/test/renderApp"

type Today = Record<string, unknown>
function today(more: Partial<Today> = {}): Today {
  return {
    counts: { new_today: 2, deadlines_this_week: 19, starred: 1, applied: 0 },
    top_picks: [row(1, { title: "HCMC AI Hackathon 2026", kind: "hackathon" }), row(2, { title: "AI Engineer Intern" })],
    needs_action: [{ message_id: "m1", subject: "Confirm your seat", action_note: "Reply by Friday", received_at: "2026-11-16T08:00:00+00:00",
                    kind: "uni_admin", urgent: true }],
    upcoming_deadlines: [row(3, { title: "Climate Ideathon", deadline: "2026-11-21" })],
    spend: { month_usd: 0.08, today_usd: 0.01, monthly_soft_cap_usd: 5, daily_cap_usd: 1 },
    status: { last_ok_check_at: "2026-11-17T14:42:00+00:00", last_check_error: null, problems: {} },
    ...more,
  }
}

function render(data: Today, answers: Record<string, unknown> = {}) {
  return renderApp("/", engine({
    "/api/today": data,
    "/api/opportunities": { tab_total: 3, total: 3, items: [] },
    "/api/emails": { items: [], truncated: false },
    ...answers,
  }))
}

afterEach(() => vi.useRealTimers())

test("the greeting follows Ho Chi Minh City time", async () => {
  vi.useFakeTimers({ now: new Date("2026-11-17T13:00:00Z"), shouldAdvanceTime: true }) // 20:00 in HCMC
  render(today())
  expect(await screen.findByRole("heading", { name: "Good evening" })).toBeInTheDocument()
  expect(screen.getByText("3 things need you today.")).toBeInTheDocument()
})

test("the four cards show the counts and open their lists", async () => {
  render(today())
  const expected: [RegExp, string, string][] = [
    [/New today/, "2", "/opportunities?tab=all&new=1"],
    [/Deadlines this week/, "19", "/opportunities?tab=all&within=6"],
    [/Starred/, "1", "/opportunities?tab=starred"],
    [/Applied/, "0", "/opportunities?tab=applied"],
  ]
  for (const [name, count, href] of expected) {
    const card = await screen.findByRole("link", { name })
    expect(card).toHaveAttribute("href", href)
    expect(card).toHaveTextContent(count)
  }
})

test("only the first top pick gets the lamp dot", async () => {
  render(today())
  await screen.findByText("AI Engineer Intern")
  expect(screen.getAllByLabelText("Top pick")).toHaveLength(1)
})

test("a top pick can be marked applied right here", async () => {
  const { calls } = render(today(), { "/api/opportunities/2/applied": () => row(2, { status: "applied" }) })
  const item = (await screen.findByText("AI Engineer Intern")).closest("[data-row]") as HTMLElement
  await userEvent.click(within(item).getByRole("button", { name: "Applied" }))
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && new URL(c.url).pathname === "/api/opportunities/2/applied")).toBe(true))
})

test("emails that need action show their note and open the email", async () => {
  render(today())
  const link = await screen.findByRole("link", { name: /Confirm your seat/ })
  expect(link).toHaveAttribute("href", "/emails?id=m1")
  expect(link).toHaveTextContent("Reply by Friday")
})

test("upcoming deadlines list their day and title", async () => {
  render(today())
  expect(await screen.findByText("Climate Ideathon")).toBeInTheDocument()
})

test("the status line shows the last check and the month's spend", async () => {
  render(today())
  expect(await screen.findByText("Checked 21:42 · Online · $0.08 of $5.00 this month")).toBeInTheDocument()
})

test("a Gmail sign-in problem shows in alert red with the fix", async () => {
  render(today({ status: { last_ok_check_at: null, last_check_error: "auth_lost", problems: { gmail: { problem: "auth_lost" } } } }))
  const text = await screen.findByText("Gmail sign-in expired")
  expect(text.closest("[data-severity]")).toHaveClass("text-alert")
  expect(screen.getByText("assistant set-key gmail")).toBeInTheDocument()
})

test("a quiet day shows the empty state", async () => {
  // Review Focus 3
  render(today({ top_picks: [], needs_action: [], upcoming_deadlines: [] }))
  expect(await screen.findByText("Nothing needs you today.")).toBeInTheDocument()
  expect(screen.getByText("No deadlines in the next 7 days.")).toBeInTheDocument()
})

test("a busy day keeps For you today short: five emails, then a link to the rest", async () => {
  const many = Array.from({ length: 29 }, (_, i) => ({ message_id: `m${i}`, subject: `Email ${i}`, action_note: "Reply", received_at: "2026-11-16T08:00:00+00:00",
                                                    kind: "news_events", urgent: false }))
  render(today({ needs_action: many }))
  await screen.findByText("Email 0")
  expect(screen.getByText("Email 4")).toBeInTheDocument()
  expect(screen.queryByText("Email 5")).toBeNull()
  expect(screen.getByRole("link", { name: "24 more emails ask for action" })).toHaveAttribute("href", "/emails")
})

test("upcoming deadlines show six, then a link to the week's list", async () => {
  const soon = Array.from({ length: 9 }, (_, i) => row(10 + i, { title: `Deadline ${i}`, deadline: "2026-11-20" }))
  render(today({ upcoming_deadlines: soon }))
  await screen.findByText("Deadline 0")
  expect(screen.getByText("Deadline 5")).toBeInTheDocument()
  expect(screen.queryByText("Deadline 6")).toBeNull()
  expect(screen.getByRole("link", { name: "3 more this week" })).toHaveAttribute("href", "/opportunities?tab=all&within=6")
})

test("a failed load says so, and Try again loads it", async () => {
  let fail = true
  render(today(), { "/api/today": () => (fail ? Response.json({ detail: "busy" }, { status: 500 }) : today()) })
  expect(await screen.findByText("Couldn't load this from the engine.")).toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  expect(await screen.findByText("3 things need you today.")).toBeInTheDocument()
})

test("the page arrives top to bottom: greeting, then cards, then rows", async () => {
  render(today())
  const heading = await screen.findByRole("heading", { level: 1 })
  const level = (element: Element) => Number((element as HTMLElement).style.getPropertyValue("--i"))
  const cards = [/New today/, /Deadlines this week/, /Starred/, /Applied/].map((name) => screen.getByRole("link", { name }))
  const rows = [...document.querySelectorAll("[data-row]")]
  expect(rows.length).toBeGreaterThan(0)
  for (const element of [heading, ...cards, ...rows]) expect(element).toHaveClass("arrive")
  expect(level(heading)).toBe(0)
  expect(Math.min(...cards.map(level))).toBeGreaterThan(level(heading))
  expect(Math.min(...rows.map(level))).toBeGreaterThan(Math.max(...cards.map(level)))
})

test("needs-action rows show their kind's dot", async () => {
  render(today())
  expect(await screen.findByRole("img", { name: "Urgent · Uni admin" })).toBeInTheDocument()
})
