import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { useAuth } from "./auth"
import { connectEvents, invalidationFor } from "./events"

/** Keeps the dashboard live: each engine event refreshes what it touched; a reconnect refreshes everything. */
export function useLiveEvents() {
  const { password, baseUrl, signOut, fetch } = useAuth()
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!password) return
    const controller = new AbortController()
    connectEvents({
      fetch,
      url: `${baseUrl}/api/events`,
      getPassword: () => password,
      onEvent: (event) => {
        for (const queryKey of invalidationFor(event)) void queryClient.invalidateQueries({ queryKey })
      },
      onReconnect: () => void queryClient.invalidateQueries(),
      onUnauthorized: signOut,
      signal: controller.signal,
    })
    return () => controller.abort()
  }, [password, baseUrl, signOut, fetch, queryClient])
}
