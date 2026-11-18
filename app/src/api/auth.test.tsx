import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect } from "react"
import { afterEach, beforeEach, expect, test, vi } from "vitest"
import { App } from "../App"
import { AuthProvider, PASSWORD_KEY, useAuth } from "./auth"
import { fakeFetch } from "../test/fakeFetch"

const SETTINGS = { connections: {}, caps: {}, for_you_min_score: 60, profile: {}, profile_path: "profile.toml" }

function setup(handler: (request: Request) => Response | Promise<Response>, children: React.ReactNode = <p>dashboard</p>) {
  const { fetch, calls } = fakeFetch(handler)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider fetch={fetch}>
        <App>{children}</App>
      </AuthProvider>
    </QueryClientProvider>,
  )
  return calls
}

function engine(request: Request) {
  const path = new URL(request.url).pathname
  const password = request.headers.get("Authorization")
  if (path === "/api/health") return Response.json({ ok: true, version: "0.1.0" })
  if (path === "/api/events" && password === "Bearer right") return new Response(new ReadableStream({ start() {} })) // stays open
  if (password !== "Bearer right") return Response.json({ detail: "no" }, { status: 401 })
  if (path === "/api/settings") return Response.json(SETTINGS)
  return Response.json({ detail: "no" }, { status: 401 }) // any other call: the password stopped working
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => vi.useRealTimers())

test("a wrong password says so and stores nothing", async () => {
  setup(engine)
  await userEvent.type(await screen.findByLabelText("Launch password"), "wrong")
  await userEvent.click(screen.getByRole("button", { name: "Connect" }))
  expect(await screen.findByText("That password didn't work. Copy it again from the engine's window.")).toBeInTheDocument()
  expect(sessionStorage.getItem(PASSWORD_KEY)).toBeNull()
  expect(localStorage.length).toBe(0)
})

test("a right password is kept in sessionStorage only", async () => {
  setup(engine)
  await userEvent.type(await screen.findByLabelText("Launch password"), "right")
  await userEvent.click(screen.getByRole("button", { name: "Connect" }))
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
  expect(sessionStorage.getItem(PASSWORD_KEY)).toBe("right")
  expect(localStorage.length).toBe(0)
})

test("a 401 later returns to the password screen", async () => {
  // Review Focus 2: the engine restarted, so the launch password changed
  sessionStorage.setItem(PASSWORD_KEY, "right")
  function CallsToday() {
    const { client } = useAuth()
    useEffect(() => {
      void client.GET("/api/today")
    }, [client])
    return <p>dashboard</p>
  }
  setup((request) => (new URL(request.url).pathname === "/api/today" ? Response.json({}, { status: 401 }) : engine(request)), <CallsToday />)
  expect(await screen.findByLabelText("Launch password")).toBeInTheDocument()
  expect(sessionStorage.getItem(PASSWORD_KEY)).toBeNull()
})

test("an unreachable engine shows the start command, then the app once it answers", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  sessionStorage.setItem(PASSWORD_KEY, "right")
  let up = false
  setup((request) => {
    if (!up) throw new TypeError("Failed to fetch")
    return engine(request)
  })
  expect(await screen.findByText("Can't reach the engine at 127.0.0.1:47615.")).toBeInTheDocument()
  expect(screen.getByText("assistant engine --dev")).toBeInTheDocument()
  up = true
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
})

test("Show password reveals what was typed, and Hide covers it again", async () => {
  setup(engine)
  const input = await screen.findByLabelText("Launch password")
  await userEvent.type(input, "right")
  expect(input).toHaveAttribute("type", "password")
  await userEvent.click(screen.getByRole("button", { name: "Show password" }))
  expect(input).toHaveAttribute("type", "text")
  await userEvent.click(screen.getByRole("button", { name: "Hide password" }))
  expect(input).toHaveAttribute("type", "password")
})

test("in dev mode the dashboard signs in by itself with the password the engine handed over", async () => {
  setup((request) => (new URL(request.url).pathname === "/__dev/launch-password" ? new Response("right\n") : engine(request)))
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
  expect(sessionStorage.getItem(PASSWORD_KEY)).toBe("right")
})

test("with nothing handed over, the password form shows", async () => {
  setup((request) => (new URL(request.url).pathname === "/__dev/launch-password" ? new Response("", { status: 404 }) : engine(request)))
  expect(await screen.findByLabelText("Launch password")).toBeInTheDocument()
})

test("a stale handed-over password falls back to the form", async () => {
  setup((request) => (new URL(request.url).pathname === "/__dev/launch-password" ? new Response("old-one") : engine(request)))
  expect(await screen.findByLabelText("Launch password")).toBeInTheDocument()
  expect(sessionStorage.getItem(PASSWORD_KEY)).toBeNull()
})

test("while the handed-over password is being tried, the form stays hidden", async () => {
  setup((request) => (new URL(request.url).pathname === "/__dev/launch-password" ? new Promise<Response>(() => {}) : engine(request)))
  expect(await screen.findByText("Signing in…")).toBeInTheDocument()
  expect(screen.queryByLabelText("Launch password")).toBeNull()
})
