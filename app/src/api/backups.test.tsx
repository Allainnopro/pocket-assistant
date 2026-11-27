import { render } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { fakeFetch } from "../test/fakeFetch"
import { inTheApp } from "../test/tauri"
import { useBackupKeeper, syncBackup } from "./backups"
import { OnlineProvider } from "./online"

const auth = vi.hoisted(() => ({ fetch: undefined as unknown as typeof globalThis.fetch }))
vi.mock("./auth", () => ({ useAuth: () => ({ password: "k", baseUrl: "https://e.ts.net", fetch: auth.fetch }) }))

const bytes = new TextEncoder().encode("gzip bytes")
const NAME = "assistant-2026-11-27.db.gz"

async function sha256(data: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("")
}

function setup(response: () => Response, have: string | null = null) {
  const { fetch, calls } = fakeFetch(response)
  const invoke = vi.fn(async (cmd: string) => (cmd === "latest_backup_date" ? have : undefined))
  const run = () => syncBackup({ fetch, baseUrl: "https://e.ts.net", key: "k", invoke: invoke as never })
  return { run, invoke, calls }
}

const backup = async (sha?: string) =>
  new Response(bytes, { headers: { "X-Backup-Date": "2026-11-27", "X-Backup-SHA256": sha ?? (await sha256(bytes)) } })

test("saves a newer backup", async () => {
  const { run, invoke, calls } = setup(await backup().then((r) => () => r.clone()))
  expect(await run()).toBe("saved")
  expect(calls[0].headers.get("Authorization")).toBe("Bearer k")
  expect(invoke).toHaveBeenCalledWith("save_backup", expect.any(Uint8Array), { headers: { "x-backup-name": NAME } })
})

test("skips when the PC already has it", async () => {
  const { run, invoke } = setup(await backup().then((r) => () => r.clone()), "2026-11-27")
  expect(await run()).toBe("current")
  expect(invoke).not.toHaveBeenCalledWith("save_backup", expect.anything(), expect.anything())
})

test("a hash mismatch saves nothing", async () => {
  const { run, invoke } = setup(await backup("0".repeat(64)).then((r) => () => r.clone()))
  expect(await run()).toBe("failed")
  expect(invoke).not.toHaveBeenCalledWith("save_backup", expect.anything(), expect.anything())
})

test("a 403 means this computer isn't the keeper", async () => {
  const { run } = setup(() => new Response("no", { status: 403 }))
  expect(await run()).toBe("not_keeper")
})

test("the unread download is cancelled when the PC already has it", async () => {
  const response = await backup()
  const cancel = vi.spyOn(response.body!, "cancel")
  const { run } = setup(() => response, "2026-11-27")
  expect(await run()).toBe("current")
  expect(cancel).toHaveBeenCalled()
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as { __TAURI__?: unknown }).__TAURI__
})

/** The keeper hook in the app, with the VM answering 403 until `keeper` is set. */
function keeperApp() {
  let keeper = false
  const invoke = vi.fn(async (cmd: string) => (cmd === "latest_backup_date" ? null : undefined))
  inTheApp(invoke)
  const { fetch, calls } = fakeFetch(async () => (keeper ? await backup() : new Response("no", { status: 403 })))
  const Probe = () => (useBackupKeeper(), null)
  auth.fetch = fetch
  const tree = (online: boolean) => <OnlineProvider value={online}><Probe /></OnlineProvider>
  return { invoke, calls, tree, setKeeper: () => void (keeper = true) }
}

test("a 403 doesn't stop the daily check: a later tick downloads once this PC is the keeper", async () => {
  vi.useFakeTimers()
  const { invoke, calls, tree, setKeeper } = keeperApp()
  render(tree(true))
  await vi.advanceTimersByTimeAsync(0)
  expect(calls).toHaveLength(1)
  expect(invoke).not.toHaveBeenCalledWith("save_backup", expect.anything(), expect.anything())
  setKeeper()
  await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000)
  expect(calls).toHaveLength(2)
  await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("save_backup", expect.any(Uint8Array), { headers: { "x-backup-name": NAME } }))
})

test("checks again when the engine comes back online", async () => {
  vi.useFakeTimers()
  const { calls, tree } = keeperApp()
  const view = render(tree(true))
  await vi.advanceTimersByTimeAsync(0)
  view.rerender(tree(false))
  await vi.advanceTimersByTimeAsync(0)
  expect(calls).toHaveLength(1) // offline: no request
  view.rerender(tree(true))
  await vi.advanceTimersByTimeAsync(0)
  expect(calls).toHaveLength(2)
})

test("a 404 or a network error fails quietly", async () => {
  expect(await setup(() => new Response("", { status: 404 })).run()).toBe("failed")
  expect(await setup(() => Promise.reject(new Error("down")) as never).run()).toBe("failed")
})
