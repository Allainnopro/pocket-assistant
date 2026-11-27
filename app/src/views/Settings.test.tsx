import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { keys } from "@/api/keys"
import { engine, renderApp } from "@/test/renderApp"

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

const TOML = 'level = "student"\n'
const INTERN = 'level = "intern"\n'
const editor = () => screen.findByRole("textbox", { name: "profile.toml" }) as Promise<HTMLTextAreaElement>
const saveButton = () => screen.getByRole("button", { name: "Save" })

test("shows profile.toml and saves an edit", async () => {
  let sent: unknown
  const { calls } = render(settings(), {
    "/api/profile/text": async (_url: URL, request: Request) => {
      if (request.method !== "PUT") return { text: TOML }
      sent = await request.json()
      return sent
    },
  })
  const box = await editor()
  expect(box.value).toBe(TOML)
  expect(box).toHaveClass("font-mono")
  fireEvent.change(box, { target: { value: INTERN } })
  fireEvent.click(saveButton())
  expect(await screen.findByText("Saved. Rankings update within 30 seconds.")).toBeInTheDocument()
  const put = calls.find((c) => c.method === "PUT")!
  expect(new URL(put.url).pathname).toBe("/api/profile/text")
  expect(sent).toEqual({ text: INTERN })
  await waitFor(() => expect(saveButton()).toBeDisabled())
  expect(box.value).toBe(INTERN)
})

test("a broken profile shows the engine's message and keeps the edits", async () => {
  render(settings(), {
    "/api/profile/text": (_url: URL, request: Request) =>
      request.method === "PUT" ? Response.json({ detail: "Expected '=' at line 1" }, { status: 400 }) : { text: TOML },
  })
  const box = await editor()
  fireEvent.change(box, { target: { value: "oops" } })
  fireEvent.click(saveButton())
  expect(await screen.findByText("Expected '=' at line 1")).toBeInTheDocument()
  expect(box.value).toBe("oops")
  expect(saveButton()).toBeEnabled()
})

test("save is disabled until something changes", async () => {
  render(settings(), { "/api/profile/text": { text: TOML } })
  const box = await editor()
  expect(saveButton()).toBeDisabled()
  fireEvent.change(box, { target: { value: TOML + "x" } })
  expect(saveButton()).toBeEnabled()
  fireEvent.change(box, { target: { value: TOML } })
  expect(saveButton()).toBeDisabled()
})

test("another device's save refreshes the text unless I'm editing", async () => {
  let text = TOML
  const { queryClient } = render(settings(), { "/api/profile/text": () => ({ text }) })
  const box = await editor()
  text = 'level = "grad"\n'
  await queryClient.invalidateQueries({ queryKey: keys.profileText })
  await waitFor(() => expect(box.value).toBe('level = "grad"\n'))
  fireEvent.change(box, { target: { value: "mine" } })
  text = 'level = "phd"\n'
  await queryClient.invalidateQueries({ queryKey: keys.profileText })
  expect(box.value).toBe("mine")
})
