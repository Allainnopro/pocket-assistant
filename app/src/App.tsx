import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useAuth } from "./api/auth"
import { unwrap } from "./api/client"
import { keys } from "./api/keys"
import { useLiveEvents } from "./api/useLiveEvents"
import { Login } from "./views/Login"
import { Unreachable } from "./views/Unreachable"

export const RETRY_MS = 3000 // while the engine is down
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
    refetchInterval: (query) => (query.state.status === "error" ? RETRY_MS : CHECK_MS),
  })
  if (health.isError) return <Unreachable />
  if (health.isPending) return null
  return <Live>{children}</Live>
}

function Live({ children }: { children: ReactNode }) {
  useLiveEvents()
  return children
}
