import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, test, vi } from "vitest"
import { App } from "../App"
import { AuthProvider } from "../api/auth"
import { fakeFetch } from "../test/fakeFetch"
import { inTheApp } from "../test/tauri"

const BASE = "https://my-vm.tail1234.ts.net"
const ADDRESS = "my-vm.tail1234.ts.net"
const SETTINGS = { connections: {}, caps: {}, for_you_min_score: 60, profile: {}, profile_path: "profile.toml" }

/** A fake cloud engine that knows one device key, "dk". */
function engine(request: Request) {
  const path = new URL(request.url).pathname
  const auth = request.headers.get("Authorization")
  if (path === "/api/health") return Response.json({ ok: true, version: "0.1.0" })
  if (path === "/api/pair") return Response.json({ device_id: 3, key: "dk" })
  if (path === "/api/events" && auth === "Bearer dk") return new Response(new ReadableStream({ start() {} }))
  if (auth !== "Bearer dk") return Response.json({ detail: "no" }, { status: 401 })
  if (path === "/api/settings") return Response.json(SETTINGS)
  return Response.json({ detail: "Not Found" }, { status: 404 })
}

/** The app's commands; `overrides` replace answers per command. */
function commands(overrides: Record<string, unknown> = {}) {
  const answers: Record<string, unknown> = {
    load_connection: null,
    check_address: BASE,
    legacy_key: null,
    save_connection: undefined,
    forget_connection: undefined,
    ...overrides,
  }
  return vi.fn((cmd: string) => {
    const answer = answers[cmd]
    return answer instanceof Error ? Promise.reject(answer.message) : Promise.resolve(answer)
  })
}

function setup(invoke: ReturnType<typeof commands>, handler: (request: Request) => Response | Promise<Response> = engine) {
  inTheApp(invoke)
  const { fetch, calls } = fakeFetch(handler)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthProvider fetch={fetch}>
        <App><p>dashboard</p></App>
      </AuthProvider>
    </QueryClientProvider>,
  )
  return calls
}

async function enterAddress() {
  await userEvent.type(await screen.findByLabelText("Engine address"), ADDRESS)
  await userEvent.click(screen.getByRole("button", { name: "Connect" }))
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => {
  delete (window as { __TAURI__?: unknown }).__TAURI__
})

test("connects with a pairing code", async () => {
  const invoke = commands()
  const calls = setup(invoke)
  await enterAddress()
  await userEvent.type(await screen.findByLabelText("Pairing code"), "123456")
  await userEvent.type(screen.getByLabelText("Name for this computer"), "laptop")
  await userEvent.click(screen.getByRole("button", { name: "Connect" }))
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
  const pair = calls.find((call) => new URL(call.url).pathname === "/api/pair")!
  expect(pair.url).toBe(`${BASE}/api/pair`)
  expect(await pair.clone().json()).toEqual({ code: "123456", device_name: "laptop" })
  expect(invoke).toHaveBeenCalledWith("save_connection", { address: ADDRESS, key: "dk" })
  expect(sessionStorage.length).toBe(0) // the device key never reaches web storage
  expect(localStorage.length).toBe(0)
})

test("uses the laptop's old key without a code", async () => {
  const invoke = commands({ legacy_key: "dk" })
  setup(invoke)
  await enterAddress()
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
  expect(invoke).toHaveBeenCalledWith("save_connection", { address: ADDRESS, key: "dk" })
  expect(screen.queryByLabelText("Pairing code")).toBeNull()
})

test("an unreachable address says so", async () => {
  const invoke = commands({ legacy_key: "dk" })
  setup(invoke, () => {
    throw new TypeError("Failed to fetch")
  })
  await enterAddress()
  expect(await screen.findByText("Can't reach my-vm.tail1234.ts.net. Is Tailscale on, on this computer?")).toBeInTheDocument()
  expect(invoke).not.toHaveBeenCalledWith("save_connection", expect.anything())
})

test("an address the app refuses shows the app's message", async () => {
  const invoke = commands({ check_address: new Error("Enter your engine's Tailscale name, like my-vm.tail1234.ts.net") })
  setup(invoke)
  await enterAddress()
  expect(await screen.findByText("Enter your engine's Tailscale name, like my-vm.tail1234.ts.net")).toBeInTheDocument()
  expect(invoke).not.toHaveBeenCalledWith("save_connection", expect.anything())
})

test("a refused code shows the engine's reason and keeps the form", async () => {
  const invoke = commands()
  setup(invoke, (request) =>
    new URL(request.url).pathname === "/api/pair" ? Response.json({ detail: "That code has expired." }, { status: 400 }) : engine(request),
  )
  await enterAddress()
  await userEvent.type(await screen.findByLabelText("Pairing code"), "123456")
  await userEvent.type(screen.getByLabelText("Name for this computer"), "laptop")
  await userEvent.click(screen.getByRole("button", { name: "Connect" }))
  expect(await screen.findByText("That code has expired.")).toBeInTheDocument()
  expect(screen.getByLabelText("Pairing code")).toHaveValue("123456")
  expect(screen.getByLabelText("Name for this computer")).toHaveValue("laptop")
  expect(invoke).not.toHaveBeenCalledWith("save_connection", expect.anything())
})

test("a saved connection signs in without showing Connect", async () => {
  const invoke = commands({ load_connection: { baseUrl: BASE, key: "dk" } })
  setup(invoke)
  expect(await screen.findByText("dashboard")).toBeInTheDocument()
  expect(screen.queryByLabelText("Engine address")).toBeNull()
})

test("a revoked key goes back to Connect and forgets it", async () => {
  const invoke = commands({ load_connection: { baseUrl: BASE, key: "revoked" } })
  setup(invoke)
  expect(await screen.findByText("This computer was disconnected. Pair it again.")).toBeInTheDocument()
  expect(invoke).toHaveBeenCalledWith("forget_connection", undefined)
  expect(screen.getByLabelText("Engine address")).toBeInTheDocument()
})
