import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { useAuth } from "./auth"
import { API_BASE } from "./client"
import { connectEvents, invalidationFor } from "./events"

/** Keeps the dashboard live: each engine event refreshes what it touched; a reconnect refreshes everything. */
export function useLiveEvents() {
  const { password, signOut, fetch } = useAuth()
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!password) return
    const controller = new AbortController()
    connectEvents({
      fetch,
      url: `${API_BASE}/api/events`,
      getPassword: () => password,
      onEvent: (event) => {
        for (const queryKey of invalidationFor(event)) void queryClient.invalidateQueries({ queryKey })
      },
      onReconnect: () => void queryClient.invalidateQueries(),
      onUnauthorized: signOut,
      signal: controller.signal,
    })
    return () => controller.abort()
  }, [password, signOut, fetch, queryClient])
}
