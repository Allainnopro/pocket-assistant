import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, type ReactNode } from "react"
import { useAuth } from "./api/auth"
import { unwrap } from "./api/client"
import { useBackupKeeper } from "./api/backups"
import { keys } from "./api/keys"
import { OnlineProvider } from "./api/online"
import { useLiveEvents } from "./api/useLiveEvents"
import { OfflineBanner } from "./components/OfflineBanner"
import { invoke, isTauri } from "./lib/tauri"
import { clock } from "./lib/time"
import { Login } from "./views/Login"
import { Unreachable } from "./views/Unreachable"

export const RETRY_MS = 3000 // while the engine is down
const APP_RETRY_MS = 30000 // the app talks to a VM: be gentle with it
const CHECK_MS = 15000 // while it's up: notice within 15 s if it stops

/** The gates in front of the dashboard: a launch password, then an engine that answers. */
export function App({ children }: { children: ReactNode }) {
  const { password } = useAuth()
  if (!password) return <Login />
  return <EngineGate>{children}</EngineGate>
}

function EngineGate({ children }: { children: ReactNode }) {
  const { client } = useAuth()
  const health = useQuery({
    queryKey: keys.health,
    queryFn: () => unwrap(client.GET("/api/health")),
    retry: false,
    refetchInterval: (query) => (query.state.status === "error" ? (isTauri() ? APP_RETRY_MS : RETRY_MS) : CHECK_MS),
  })
  const since = useRef<string | null>(null) // HH:MM (HCMC) of the first failure; kept until a real success
  if (health.isError) since.current ??= clock(new Date().toISOString())
  else if (health.isSuccess) since.current = null // a retry with no data yet flips to pending: still down
  const failedAt = since.current
  const online = failedAt === null
  const known = !(health.isPending && online) // first answer not in yet

  useEffect(() => {
    if (!known || !isTauri()) return
    invoke("report_connection", { online, since: failedAt }).catch(() => {}) // the tray tooltip is a courtesy
  }, [known, online, failedAt])

  if (health.isPending) return null
  if (health.data === undefined) return <Unreachable />
  return (
    <OnlineProvider value={online}>
      {failedAt && <OfflineBanner since={failedAt} />}
      <Live>{children}</Live>
    </OnlineProvider>
  )
}

function Live({ children }: { children: ReactNode }) {
  useLiveEvents()
  useBackupKeeper()
  return children
}
