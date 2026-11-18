import { screen, within } from "@testing-library/react"
import { expect, test } from "vitest"
import { engine, renderApp } from "@/test/renderApp"

const SPEND = {
  days: [
    { day: "2026-11-02", cost_usd: 0.75, calls: 2, input_tokens: 3000, output_tokens: 400 },
    { day: "2026-11-03", cost_usd: 0.125, calls: 1, input_tokens: 1000, output_tokens: 200 },
  ],
  month_usd: 0.875,
  monthly_soft_cap_usd: 5,
}
const ITEMS = {
  weeks: [
    { week_start: "2026-11-02", by_kind: { hackathon: 3, internship: 1 }, by_source: { devpost: 3, linkedin: 1 } },
    { week_start: "2026-11-09", by_kind: { job: 2 }, by_source: { linkedin: 2 } },
  ],
}

function render(digests: unknown[] = []) {
  return renderApp("/reports", engine({
    "/api/reports/spend": SPEND,
    "/api/reports/digests": { digests },
    "/api/reports/items": ITEMS,
    "/api/opportunities": { tab_total: 0, total: 0, items: [] },
    "/api/emails": { items: [], truncated: false },
  }))
}

test("the month's spend reads against the cap", async () => {
  render()
  expect(await screen.findByText("$0.88 of $5.00 this month")).toBeInTheDocument()
  expect(screen.getByRole("progressbar", { name: "Monthly budget used" })).toHaveAttribute("aria-valuenow", "18")
  expect(screen.getByText("4,000 tokens in · 600 out")).toBeInTheDocument()
})

test("no digests shows the empty line", async () => {
  render()
  expect(await screen.findByText("No digests yet. They start with Telegram.")).toBeInTheDocument()
})

test("past digests list their date, items and whether they were sent", async () => {
  render([{ digest_date: "2026-11-16", created_at: "2026-11-16 01:00:00", sent_at: null, send_error: "chat not found", item_count: 4 }])
  const digest = await screen.findByText("2026-11-16")
  const item = digest.closest("[data-digest]") as HTMLElement
  expect(within(item).getByText("4 items")).toBeInTheDocument()
  expect(within(item).getByText("Not sent: chat not found")).toBeInTheDocument()
})

test("items per week list each week's kinds and sources", async () => {
  render()
  const kinds = await screen.findByRole("table", { name: "Items per kind each week" })
  expect(within(kinds).getByRole("columnheader", { name: "Hackathon" })).toBeInTheDocument()
  const firstWeek = within(kinds).getByRole("row", { name: /Mon 2 Nov/ })
  expect(within(firstWeek).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3", "1", "0"])
  const sources = screen.getByRole("table", { name: "Items per source each week" })
  expect(within(sources).getByRole("row", { name: /Mon 9 Nov/ })).toHaveTextContent("2")
})
