import { vi } from "vitest"

/** A stand-in for fetch: tests never reach the real engine. Records every request. */
export function fakeFetch(handler: (request: Request) => Response | Promise<Response>) {
  const calls: Request[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    calls.push(request)
    return handler(request)
  })
  return { fetch: fetch as unknown as typeof globalThis.fetch, calls }
}
