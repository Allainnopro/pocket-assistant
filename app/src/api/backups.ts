import { useEffect } from "react"
import { invoke, isTauri } from "@/lib/tauri"
import { useAuth } from "./auth"

type Result = "saved" | "current" | "not_keeper" | "failed"
type Deps = { fetch: typeof fetch; baseUrl: string; key: string; invoke: typeof invoke }

const DAY_MS = 24 * 60 * 60 * 1000

const hex = (buffer: ArrayBuffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("")

/** Downloads the engine's newest backup when this PC lacks it and keeps it if the SHA-256 matches. Never throws. */
export async function syncBackup({ fetch, baseUrl, key, invoke }: Deps): Promise<Result> {
  try {
    const response = await fetch(`${baseUrl}/api/backup/latest`, { headers: { Authorization: `Bearer ${key}` } })
    if (response.status === 403) return "not_keeper"
    if (!response.ok) return "failed"
    const date = response.headers.get("X-Backup-Date") ?? ""
    const sha = (response.headers.get("X-Backup-SHA256") ?? "").toLowerCase()
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return "failed"
    if (date === (await invoke<string | null>("latest_backup_date"))) return "current"
    const bytes = new Uint8Array(await response.arrayBuffer())
    // a cut-off or corrupted download fails here, before anything touches the disk
    if (hex(await crypto.subtle.digest("SHA-256", bytes)) !== sha) return "failed"
    await invoke("save_backup", bytes, { headers: { "x-backup-name": `assistant-${date}.db.gz` } })
    return "saved"
  } catch {
    return "failed" // offline or disk trouble: the next daily try starts over
  }
}

/** In the desktop app: keep the nightly backup on this PC. Once now, then daily; stops for the session if this
 * computer is not the keeper. */
export function useBackupKeeper() {
  const { password, baseUrl, fetch } = useAuth()
  useEffect(() => {
    if (!isTauri() || !password) return
    let stopped = false
    const run = async () => {
      if (!stopped && (await syncBackup({ fetch: fetch ?? globalThis.fetch.bind(globalThis), baseUrl, key: password, invoke })) === "not_keeper") {
        stopped = true
        clearInterval(timer)
      }
    }
    const timer = setInterval(() => void run(), DAY_MS)
    void run()
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [password, baseUrl, fetch])
}
