import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, waitFor } from "@testing-library/react"
import { beforeEach, expect, test, vi } from "vitest"
import { fakeFetch } from "../test/fakeFetch"
import { AuthProvider, PASSWORD_KEY } from "./auth"
import { useLiveEvents } from "./useLiveEvents"

function Live() {
  useLiveEvents()
  return null
}

beforeEach(() => sessionStorage.setItem(PASSWORD_KEY, "pw"))

test("a star on the other computer refreshes this item and the lists", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: item_changed\ndata: {"id": 7, "action": "star"}\n\n'))
    },
  })
  const { fetch, calls } = fakeFetch(() => new Response(body, { headers: { "content-type": "text/event-stream" } }))
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider fetch={fetch}><Live /></AuthProvider>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["opportunity", 7] }))
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["opportunities"] })
  expect(new URL(calls[0].url).pathname).toBe("/api/events")
  expect(calls[0].headers.get("Authorization")).toBe("Bearer pw")
})
