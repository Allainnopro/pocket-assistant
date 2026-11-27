import { useEffect } from "react"
import { invoke, isTauri } from "@/lib/tauri"
import { useAuth } from "./auth"
import { useOnline } from "./online"

type Result = "saved" | "current" | "not_keeper" | "failed"
type Deps = { fetch: typeof fetch; baseUrl: string; key: string; invoke: typeof invoke }

const DAY_MS = 24 * 60 * 60 * 1000

const hex = (buffer: ArrayBuffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("")

/** Downloads the engine's newest backup when this PC lacks it and keeps it if the SHA-256 matches. Never throws. */
export async function syncBackup({ fetch, baseUrl, key, invoke }: Deps): Promise<Result> {
  try {
    const response = await fetch(`${baseUrl}/api/backup/latest`, { headers: { Authorization: `Bearer ${key}` } })
    const skip = (result: Result) => {
      void response.body?.cancel() // don't leave the download hanging open
      return result
    }
    if (response.status === 403) return skip("not_keeper")
    if (!response.ok) return skip("failed")
    const date = response.headers.get("X-Backup-Date") ?? ""
    const sha = (response.headers.get("X-Backup-SHA256") ?? "").toLowerCase()
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return skip("failed")
    if (date === (await invoke<string | null>("latest_backup_date"))) return skip("current")
    const bytes = new Uint8Array(await response.arrayBuffer())
    // a cut-off or corrupted download fails here, before anything touches the disk
    if (hex(await crypto.subtle.digest("SHA-256", bytes)) !== sha) return "failed"
    await invoke("save_backup", bytes, { headers: { "x-backup-name": `assistant-${date}.db.gz` } })
    return "saved"
  } catch {
    return "failed" // offline or disk trouble: the next daily try starts over
  }
}

/** In the desktop app: keep the nightly backup on this PC. Checks when the engine is reachable, then daily. A 403
 * (this computer isn't the keeper yet) is tried again each day and after each reconnect, so naming the keeper later
 * on the VM is picked up without relaunching the app. */
export function useBackupKeeper() {
  const { password, baseUrl, fetch } = useAuth()
  const online = useOnline()
  useEffect(() => {
    if (!isTauri() || !password || !online) return
    let stopped = false
    const run = () => {
      if (!stopped) void syncBackup({ fetch: fetch ?? globalThis.fetch.bind(globalThis), baseUrl, key: password, invoke })
    }
    const timer = setInterval(run, DAY_MS)
    run()
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [password, baseUrl, fetch, online])
}
