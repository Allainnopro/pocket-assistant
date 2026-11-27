import { expect, test, vi } from "vitest"
import { fakeFetch } from "../test/fakeFetch"
import { syncBackup } from "./backups"

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

test("a 404 or a network error fails quietly", async () => {
  expect(await setup(() => new Response("", { status: 404 })).run()).toBe("failed")
  expect(await setup(() => Promise.reject(new Error("down")) as never).run()).toBe("failed")
})
