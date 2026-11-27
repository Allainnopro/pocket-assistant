import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
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

let up = true
const answers = engine({
  "/api/health": { ok: true },
  "/api/settings": {},
  "/api/opportunities": { tab_total: 1, total: 1, items: [row(1)] },
  "/api/opportunities/1": detail(1, { title: "HCMC AI Hackathon 2026" }),
  "/api/opportunities/1/star": row(1, { starred: true }),
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
function setup() {
  sessionStorage.setItem(PASSWORD_KEY, "pw")
  const { fetch, calls } = fakeFetch((request) => {
    if (!up && !request.url.endsWith("/api/settings")) throw new TypeError("Failed to fetch")
    return answers(request)
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ["/opportunities?tab=all&id=1"] })
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
  expect(calls.some((call) => call.url.includes("/star"))).toBe(false)
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
