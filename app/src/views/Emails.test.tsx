import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"
import { row } from "@/test/fixtures"
import { engine, location, renderApp } from "@/test/renderApp"

type Email = Record<string, unknown>
function email(id: string, more: Email = {}): Email {
  return { message_id: id, received_at: "2026-11-16T18:00:00+00:00", sender_name: "Phòng Đào tạo", subject: `Subject ${id}`,
           excerpt: "Lịch thi học kỳ 1 đã được cập nhật.", source: "university", kind: "news_events", urgent: false, badges: [], action_note: null,
           opportunity_count: 0, ...more }
}

function render(path: string, items: Email[], more: Record<string, unknown> = {}) {
  return renderApp(path, engine({
    "/api/emails": (url: URL) => ({ items: url.searchParams.get("section") === "other" ? [] : items, truncated: false }),
    "/api/opportunities": { tab_total: 0, total: 0, items: [] },
    ...more,
  }))
}

afterEach(() => vi.useRealTimers())

test("emails are grouped by Ho Chi Minh City day", async () => {
  vi.useFakeTimers({ now: new Date("2026-11-17T03:00:00Z"), shouldAdvanceTime: true }) // Tue 17 Nov, 10:00
  render("/emails", [
    email("a", { received_at: "2026-11-16T18:00:00+00:00" }), // 01:00 on the 17th in HCMC
    email("b", { received_at: "2026-11-16T08:00:00+00:00" }),
    email("c", { received_at: "2026-11-14T08:00:00+00:00" }),
  ])
  for (const day of ["Today", "Yesterday", "Sat 14 Nov"]) expect(await screen.findByRole("heading", { name: day })).toBeInTheDocument()
})

test("rows show the sender, subject and excerpt", async () => {
  render("/emails", [email("a", { subject: "Lịch thi học kỳ 1" })])
  const subject = await screen.findByText("Lịch thi học kỳ 1")
  const item = subject.closest("[data-row]") as HTMLElement
  expect(within(item).getByText("Phòng Đào tạo")).toBeInTheDocument()
  expect(within(item).getByText("Lịch thi học kỳ 1 đã được cập nhật.")).toBeInTheDocument()
  expect(within(item).getByRole("img", { name: "Uni news & events" })).toBeInTheDocument() // the dot, not a badge
})

test("the Other tab asks for section=other", async () => {
  const { calls } = render("/emails", [email("a")])
  await screen.findByText("Subject a")
  await userEvent.click(screen.getByRole("tab", { name: "Other" }))
  await waitFor(() => expect(calls.some((c) => new URL(c.url).searchParams.get("section") === "other")).toBe(true))
  expect(await screen.findByText("No other email in the last 7 days.")).toBeInTheDocument()
})

test("the reading pane shows the note, the opportunities found and Open in Gmail", async () => {
  render("/emails?id=m1", [email("m1", { badges: ["opportunity", "needs_action"] })], {
    "/api/emails/m1": { ...email("m1", { subject: "Confirm your seat", badges: ["opportunity", "needs_action"], action_note: "Reply by Friday" }),
                        opportunities: [row(5, { title: "HCMC AI Hackathon 2026" })], gmail_url: "https://mail.google.com/mail/#all/m1" },
  })
  const pane = await screen.findByRole("complementary", { name: "Confirm your seat" })
  expect(within(pane).getByText("Reply by Friday")).toBeInTheDocument()
  expect(within(pane).getByRole("link", { name: /HCMC AI Hackathon 2026/ })).toHaveAttribute("href", "/opportunities?tab=all&id=5")
  expect(within(pane).getByRole("link", { name: /Open in Gmail/ })).toHaveAttribute("href", "https://mail.google.com/mail/#all/m1")
})

test("Esc closes the pane and Enter opens the selected email", async () => {
  const { router } = render("/emails", [email("a"), email("b")], { "/api/emails/a": { ...email("a"), opportunities: [], gmail_url: "https://mail.google.com/mail/#all/a" } })
  await screen.findByText("Subject b")
  await userEvent.keyboard("j{Enter}")
  expect(location(router)).toBe("/emails?id=a")
  await screen.findByRole("complementary", { name: "Subject a" })
  await userEvent.keyboard("{Escape}")
  expect(location(router)).toBe("/emails")
})

test("an email that addressed the assistant is flagged in alert red", async () => {
  render("/emails", [email("a", { badges: ["addressed_ai"] })])
  const badge = await screen.findByText("Addressed the assistant")
  expect(badge.closest("[data-badge]")).toHaveClass("text-alert")
})

test("a truncated window says so", async () => {
  render("/emails", [], { "/api/emails": { items: [email("a")], truncated: true } })
  expect(await screen.findByText("Showing the newest 500 in this window.")).toBeInTheDocument()
})

test("no emails in the window shows the empty state", async () => {
  // Review Focus 3
  render("/emails", [])
  expect(await screen.findByText("No important email in the last 7 days.")).toBeInTheDocument()
})

test("a failed load says so instead of a blank list, and Try again loads it", async () => {
  let fail = true
  render("/emails", [email("m1", { subject: "Confirm your seat" })], {
    "/api/emails": () => (fail ? Response.json({ detail: "busy" }, { status: 500 }) : { items: [email("m1", { subject: "Confirm your seat" })], truncated: false }),
  })
  expect(await screen.findByText("Couldn't load this from the engine.")).toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  expect(await screen.findByText("Confirm your seat")).toBeInTheDocument()
})

test("j and k bring the selected email into view", async () => {
  const scrolled = vi.spyOn(Element.prototype, "scrollIntoView")
  render("/emails", [email("a"), email("b")])
  await screen.findByText("Subject b")
  await userEvent.keyboard("jj")
  expect(scrolled.mock.contexts.at(-1)).toBe(document.querySelector('[data-row="b"]'))
  expect(scrolled).toHaveBeenLastCalledWith({ block: "nearest" })
  scrolled.mockRestore()
})

test("each row shows its kind's dot, and the legend lists the kinds in view", async () => {
  render("/emails", [email("a", { kind: "jobs" }), email("b", { kind: "study", urgent: true })])
  await screen.findByText("Subject b")
  expect(within(document.querySelector('[data-row="a"]') as HTMLElement).getByRole("img", { name: "Jobs & internships" })).toBeInTheDocument()
  expect(within(document.querySelector('[data-row="b"]') as HTMLElement).getByRole("img", { name: "Urgent · Subjects & program" })).toBeInTheDocument()
  const legend = screen.getByRole("list", { name: "Kinds" })
  expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["Subjects & program", "Jobs & internships"])
})

test("the email pane names the kind beside the subject", async () => {
  render("/emails?id=a", [email("a", { kind: "scholarship" })],
         { "/api/emails/a": { ...email("a", { kind: "scholarship" }), opportunities: [], gmail_url: "https://mail.google.com/mail/#all/a" } })
  const pane = await screen.findByRole("complementary", { name: "Subject a" })
  expect(within(pane).getByText("Scholarships")).toBeInTheDocument()
  expect(within(pane).getByRole("img", { name: "Scholarships" })).toBeInTheDocument()
})
