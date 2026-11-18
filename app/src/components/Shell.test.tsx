import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, test } from "vitest"
import { engine, location, renderApp } from "@/test/renderApp"

const SEARCH = {
  results: [
    { type: "opportunity", id: "5", title: "AI Hackathon 2026", subtitle: "Devpost" },
    { type: "email", id: "m1", title: "AI internship news", subtitle: "Example Robotics" },
    { type: "page", id: "reports", title: "Reports", subtitle: "Page" },
  ],
}
const quiet = engine({
  "/api/opportunities": { tab_total: 12, total: 12, items: [] },
  "/api/emails": { items: [], truncated: false },
  "/api/search": (url: URL) => (url.searchParams.get("q") === "ai" ? SEARCH : { results: [] }),
})

test("the lamp marker sits on the current page and follows it", async () => {
  // jsdom has no layout: give the links the positions a browser would
  const top = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetTop")
  Object.defineProperty(HTMLElement.prototype, "offsetTop", {
    configurable: true,
    get(this: HTMLElement) {
      const text = this.textContent ?? ""
      return this.tagName === "A" ? (text.startsWith("Opportunities") ? 40 : text.startsWith("Emails") ? 80 : 0) : 0
    },
  })
  try {
    renderApp("/opportunities", quiet)
    const current = await screen.findByRole("link", { name: /Opportunities/ })
    expect(current).toHaveAttribute("aria-current", "page")
    const marker = document.querySelector("[data-marker]") as HTMLElement
    await waitFor(() => expect(marker.style.transform).toBe("translateY(40px)"))
    act(() => {
      fireEvent.keyDown(window, { key: "g" })
      fireEvent.keyDown(window, { key: "e" })
    })
    await waitFor(() => expect(marker.style.transform).toBe("translateY(80px)"))
    for (const link of screen.getAllByRole("link")) expect(link.className).not.toMatch(/--lamp/) // the marker carries it
  } finally {
    if (top) Object.defineProperty(HTMLElement.prototype, "offsetTop", top)
  }
})

test("the sidebar shows the For you count", async () => {
  renderApp("/", quiet)
  const link = await screen.findByRole("link", { name: /Opportunities/ })
  await waitFor(() => expect(link).toHaveTextContent("12"))
})

test("g o and g e jump between pages", async () => {
  const { router } = renderApp("/", quiet)
  await screen.findByRole("link", { name: /Today/ })
  act(() => {
    fireEvent.keyDown(window, { key: "g" })
    fireEvent.keyDown(window, { key: "o" })
  })
  expect(location(router)).toBe("/opportunities")
  act(() => {
    fireEvent.keyDown(window, { key: "g" })
    fireEvent.keyDown(window, { key: "e" })
  })
  expect(location(router)).toBe("/emails")
})

test("Ctrl+K opens the palette; typing shows results with their type", async () => {
  renderApp("/", quiet)
  await screen.findByRole("link", { name: /Today/ })
  act(() => {
    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
  })
  const input = await screen.findByPlaceholderText("Search opportunities, emails and pages…")
  await userEvent.type(input, "ai")
  const opportunity = await screen.findByRole("option", { name: /AI Hackathon 2026/ })
  expect(opportunity).toHaveTextContent("Opportunity")
  expect(screen.getByRole("option", { name: /AI internship news/ })).toHaveTextContent("Email")
  expect(screen.getByRole("option", { name: /Reports/ })).toHaveTextContent("Page")
})

test("Enter on an opportunity opens it in the panel route", async () => {
  const { router } = renderApp("/", quiet)
  await screen.findByRole("link", { name: /Today/ })
  act(() => {
    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
  })
  await userEvent.type(await screen.findByPlaceholderText("Search opportunities, emails and pages…"), "ai")
  await screen.findByRole("option", { name: /AI Hackathon 2026/ })
  await userEvent.keyboard("{Enter}")
  await waitFor(() => expect(location(router)).toBe("/opportunities?tab=all&id=5"))
})

test("the slash key opens the palette too, but not while typing in it", async () => {
  renderApp("/", quiet)
  await screen.findByRole("link", { name: /Today/ })
  act(() => {
    fireEvent.keyDown(window, { key: "/" })
  })
  const input = await screen.findByPlaceholderText("Search opportunities, emails and pages…")
  await userEvent.type(input, "g/")
  expect(input).toHaveValue("g/")
})
