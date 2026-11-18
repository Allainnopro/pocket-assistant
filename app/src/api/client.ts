import createClient, { type Client, type Middleware } from "openapi-fetch"
import type { paths } from "./schema"

/** The engine's API. `[api] port` in config.toml; VITE_API_BASE overrides it for development. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:47615"

export type ApiClient = Client<paths>

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

/** One typed client. Every call carries the launch password; a 401 means it stopped working. */
export function makeClient(opts: {
  fetch?: typeof globalThis.fetch
  getPassword: () => string | null
  onUnauthorized: () => void
}): ApiClient {
  const client = createClient<paths>({ baseUrl: API_BASE, fetch: opts.fetch })
  const auth: Middleware = {
    onRequest({ request }) {
      const password = opts.getPassword()
      if (password) request.headers.set("Authorization", `Bearer ${password}`)
      return request
    },
    onResponse({ response }) {
      if (response.status === 401) opts.onUnauthorized()
      return response
    },
  }
  client.use(auth)
  return client
}

function message(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  return typeof detail === "string" ? detail : fallback
}

/** The data of a 2xx answer; anything else throws ApiError, so queries and mutations fail properly. */
export async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call
  if (!response.ok || data === undefined) throw new ApiError(response.status, message(error, response.statusText))
  return data
}
