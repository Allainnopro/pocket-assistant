import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { engine, renderApp } from "@/test/renderApp"
import { inTheApp } from "@/test/tauri"

type Settings = Record<string, unknown>
function settings(gmail: Record<string, unknown> = {}, more: Settings = {}): Settings {
  return {
    connections: {
      gmail: { address: "me@gmail.com", signed_in: true, last_ok_check_at: "2026-11-17T14:42:00+00:00", ...gmail },
      anthropic: { key_saved: true },
      telegram: { paired: false },
    },
    caps: { monthly_soft_usd: 5, daily_usd: 1 },
    for_you_min_score: 60,
    profile: { level: "student", interests: ["machine learning", "LLM apps"], open_to: ["hackathon", "internship"], home: "Ho Chi Minh City", remote_ok: "yes" },
    profile_path: "C:\\Users\\Me\\pocket-assistant\\profile.toml",
    ...more,
  }
}

function render(data: Settings, answers: Record<string, unknown> = {}) {
  return renderApp("/settings", engine({
    "/api/settings": data,
    "/api/opportunities": { tab_total: 0, total: 0, items: [] },
    "/api/emails": { items: [], truncated: false },
    ...answers,
  }))
}

afterEach(() => vi.restoreAllMocks())

test("Gmail shows signed in", async () => {
  render(settings())
  const gmail = (await screen.findByText("Gmail")).closest("[data-connection]") as HTMLElement
  expect(within(gmail).getByText("me@gmail.com")).toBeInTheDocument()
  expect(within(gmail).getByText("Signed in")).toBeInTheDocument()
})

test("an expired Gmail sign-in is an alert with the command to fix it", async () => {
  render(settings({ signed_in: false }))
  const gmail = (await screen.findByText("Gmail")).closest("[data-connection]") as HTMLElement
  expect(within(gmail).getByText("Sign-in expired").closest("[data-severity]")).toHaveClass("text-alert")
  expect(within(gmail).getByText("assistant set-key gmail")).toBeInTheDocument()
})

test("Telegram has no button yet", async () => {
  render(settings())
  const telegram = (await screen.findByText("Telegram")).closest("[data-connection]") as HTMLElement
  expect(within(telegram).getByText("Arrives with Telegram")).toBeInTheDocument()
  expect(within(telegram).queryByRole("button")).toBeNull()
})

test("limits are read-only and name their config.toml keys", async () => {
  render(settings())
  expect(await screen.findByText("[llm] monthly_soft_cap_usd")).toBeInTheDocument()
  expect(screen.getByText("[llm] daily_cap_usd")).toBeInTheDocument()
  expect(screen.getByText("[dashboard] for_you_min_score")).toBeInTheDocument()
  expect(screen.getByText("$5.00")).toBeInTheDocument()
  expect(screen.getByText("Restart the engine after changing config.toml.")).toBeInTheDocument()
  expect(screen.queryAllByRole("textbox")).toHaveLength(0)
  expect(screen.queryAllByRole("spinbutton")).toHaveLength(0)
})

test("the profile summary is shown", async () => {
  render(settings())
  expect(await screen.findByText("machine learning, LLM apps")).toBeInTheDocument()
  expect(screen.getByText("Hackathon, Internship")).toBeInTheDocument()
})

test("Copy puts the profile path on the clipboard", async () => {
  const writeText = vi.fn(async () => {})
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
  render(settings())
  const path = await screen.findByText("C:\\Users\\Me\\pocket-assistant\\profile.toml")
  fireEvent.click(within(path.closest("[data-copy]") as HTMLElement).getByRole("button", { name: "Copy" }))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("C:\\Users\\Me\\pocket-assistant\\profile.toml"))
})

test("Rescore reports what it did", async () => {
  const { calls } = render(settings(), { "/api/settings/rescore": { rescored: 12, expired: 1 } })
  fireEvent.click(await screen.findByRole("button", { name: "Rescore" }))
  expect(await screen.findByText("Rescored 12 · 1 expired")).toBeInTheDocument()
  expect(calls.some((c) => c.method === "POST" && new URL(c.url).pathname === "/api/settings/rescore")).toBe(true)
})

test("a failed load says so, and Try again loads it", async () => {
  let fail = true
  render(settings(), { "/api/settings": () => (fail ? Response.json({ detail: "busy" }, { status: 500 }) : settings()) })
  expect(await screen.findByText("Couldn't load this from the engine.")).toBeInTheDocument()
  fail = false
  fireEvent.click(screen.getByRole("button", { name: "Try again" }))
  expect(await screen.findByText("Connected accounts")).toBeInTheDocument()
})

test("Settings is a plain list split by bold headings, not boxes", async () => {
  render(settings())
  for (const name of ["Connected accounts", "Limits", "Profile"]) {
    const heading = await screen.findByRole("heading", { level: 2, name })
    expect(heading).toHaveClass("font-semibold")
    const section = heading.closest("section") as HTMLElement
    expect(section).not.toHaveClass("rounded-xl")
    expect(section).not.toHaveClass("border")
  }
})

test("in the desktop app, Settings can open the profile file", async () => {
  const invoke = vi.fn(() => Promise.resolve())
  inTheApp(invoke)
  try {
    render(settings())
    fireEvent.click(await screen.findByRole("button", { name: "Open" }))
    expect(invoke).toHaveBeenCalledWith("open_profile", { path: "C:\\Users\\Me\\pocket-assistant\\profile.toml" })
  } finally {
    delete (window as { __TAURI__?: unknown }).__TAURI__
  }
})

test("in the browser there is no Open button for the profile", async () => {
  render(settings())
  await screen.findByText("Profile")
  expect(screen.queryByRole("button", { name: "Open" })).toBeNull()
})
