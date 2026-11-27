import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter } from "react-router"
import { RouterProvider } from "react-router/dom"
import { afterEach, beforeEach, expect, test, vi } from "vitest"
import { AuthProvider, PASSWORD_KEY } from "@/api/auth"
import { keys } from "@/api/keys"
import { TooltipProvider } from "@/components/ui/tooltip"
import { routes } from "@/router"
import { fakeFetch } from "@/test/fakeFetch"
import { detail, row } from "@/test/fixtures"
import { engine } from "@/test/renderApp"
import { inTheApp } from "@/test/tauri"
import { App } from "./App"

const settings = {
  connections: { gmail: { address: "me@gmail.com", signed_in: true, last_ok_check_at: null }, anthropic: { key_saved: true }, telegram: { paired: false } },
  caps: { monthly_soft_usd: 5, daily_usd: 1 }, for_you_min_score: 60, profile_path: "profile.toml",
  profile: { level: "student", interests: [], open_to: [], home: "Ho Chi Minh City", remote_ok: "yes" },
}
let up = true
const answers = engine({
  "/api/health": { ok: true },
  "/api/settings": settings,
  "/api/opportunities": { tab_total: 1, total: 1, items: [row(1)] },
  "/api/opportunities/1": detail(1, { title: "HCMC AI Hackathon 2026" }),
  "/api/opportunities/1/star": row(1, { starred: true }),
  "/api/settings/rescore": { rescored: 1, expired: 0 },
  "/api/emails": { items: [], truncated: false },
})

/** The desktop app: it signs in with the connection it remembers, and reports to the tray. */
function app() {
  const invoke = vi.fn((cmd: string) =>
    Promise.resolve(cmd === "load_connection" ? { baseUrl: "https://my-vm.tail1234.ts.net", key: "dk" } : undefined))
  inTheApp(invoke)
  return invoke
}

/** The dashboard behind the engine gate, on an engine that can be switched off (sign-in still answers). */
function setup(path = "/opportunities?tab=all&id=1") {
  sessionStorage.setItem(PASSWORD_KEY, "pw")
  const { fetch, calls } = fakeFetch((request) => {
    if (!up && !request.url.endsWith("/api/settings")) throw new TypeError("Failed to fetch")
    return answers(request)
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AuthProvider fetch={fetch}>
          <App><RouterProvider router={router} /></App>
        </AuthProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  const check = () => act(() => queryClient.refetchQueries({ queryKey: keys.health }))
  return { calls, check }
}

beforeEach(() => {
  up = true
  sessionStorage.clear()
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(new Date("2026-11-27T03:42:00Z")) // 10:42 in HCMC
})
afterEach(() => {
  vi.useRealTimers()
  delete (window as { __TAURI__?: unknown }).__TAURI__
})

test("data stays and a banner shows when the engine goes away", async () => {
  const { check } = setup()
  expect(await screen.findByText("HCMC AI Hackathon 2026", { selector: "h2, h1, h3" })).toBeInTheDocument()
  up = false
  await check()
  expect(await screen.findByText("Engine unreachable since 10:42 · retrying")).toBeInTheDocument()
  expect(screen.getByText("HCMC AI Hackathon 2026", { selector: "h2, h1, h3" })).toBeInTheDocument()
})

test("actions are disabled offline and send no request", async () => {
  const { calls, check } = setup()
  const star = await screen.findByRole("button", { name: /Star/ })
  expect(star).toBeEnabled()
  up = false
  await check()
  await waitFor(() => expect(screen.getByRole("button", { name: /Star/ })).toBeDisabled())
  await userEvent.click(screen.getByRole("button", { name: /Star/ }))
  act(() => {
    fireEvent.keyDown(window, { key: "s" }) // the keyboard shortcut goes through the same gate
  })
  expect(calls.some((call) => call.url.includes("/star"))).toBe(false)
})

test("Rescore is disabled offline and sends no request", async () => {
  const { calls, check } = setup("/settings")
  expect(await screen.findByRole("button", { name: "Rescore" })).toBeEnabled()
  up = false
  await check()
  await waitFor(() => expect(screen.getByRole("button", { name: "Rescore" })).toBeDisabled())
  await userEvent.click(screen.getByRole("button", { name: "Rescore" }))
  expect(calls.some((call) => call.url.includes("/rescore"))).toBe(false)
})

test("back online clears the banner and re-enables actions", async () => {
  const { check } = setup()
  await screen.findByRole("button", { name: /Star/ })
  up = false
  await check()
  await screen.findByText(/Engine unreachable/)
  up = true
  await check()
  await waitFor(() => expect(screen.queryByText(/Engine unreachable/)).toBeNull())
  expect(screen.getByRole("button", { name: /Star/ })).toBeEnabled()
})

test("never loaded shows the unreachable screen", async () => {
  up = false
  setup()
  expect(await screen.findByText("Can't reach the engine at 127.0.0.1:47615.")).toBeInTheDocument()
  expect(screen.queryByText(/Engine unreachable/)).toBeNull()
})

test("in the app, never loaded names the address, not the tray", async () => {
  up = false
  app()
  setup()
  expect(await screen.findByText("Can't reach the engine at my-vm.tail1234.ts.net. Retrying…")).toBeInTheDocument()
  expect(screen.queryByText(/tray/)).toBeNull()
})

test("the tray is told the state", async () => {
  const invoke = app()
  const { check } = setup()
  await screen.findByRole("button", { name: /Star/ })
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("report_connection", { online: true, since: null }))
  up = false
  await check()
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("report_connection", { online: false, since: "10:42" }))
  up = true
  await check()
  await waitFor(() => expect(invoke).toHaveBeenLastCalledWith("report_connection", { online: true, since: null }))
})

test("a long first outage reports once, with the first failure's time", async () => {
  const invoke = app()
  up = false
  const { calls, check } = setup()
  await screen.findByText("Can't reach the engine at my-vm.tail1234.ts.net. Retrying…")
  vi.setSystemTime(new Date("2026-11-27T03:50:00Z"))
  await check()
  await check()
  await screen.findByText(/Retrying/)
  expect(calls.filter((call) => call.url.endsWith("/api/health")).length).toBeGreaterThan(2)
  const reports = invoke.mock.calls.filter(([cmd]) => cmd === "report_connection")
  expect(reports).toEqual([["report_connection", { online: false, since: "10:42" }]])
})
