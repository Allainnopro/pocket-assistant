import { describe, expect, test, vi } from "vitest"
import { fakeFetch } from "../test/fakeFetch"
import { ApiError, makeClient, unwrap } from "./client"

describe("makeClient", () => {
  test("adds the bearer header", async () => {
    const { fetch, calls } = fakeFetch(() => Response.json({ ok: true, version: "0.1.0" }))
    const client = makeClient({ fetch, getPassword: () => "pw", onUnauthorized: () => {} })
    await client.GET("/api/health")
    expect(calls[0].headers.get("Authorization")).toBe("Bearer pw")
    expect(calls[0].url).toBe("http://127.0.0.1:47615/api/health")
  })

  test("sends no header without a password", async () => {
    const { fetch, calls } = fakeFetch(() => Response.json({ ok: true, version: "0.1.0" }))
    await makeClient({ fetch, getPassword: () => null, onUnauthorized: () => {} }).GET("/api/health")
    expect(calls[0].headers.has("Authorization")).toBe(false)
  })

  test("a 401 calls onUnauthorized and unwrap throws ApiError 401", async () => {
    const onUnauthorized = vi.fn()
    const { fetch } = fakeFetch(() => Response.json({ detail: "A valid password or device key is needed." }, { status: 401 }))
    const client = makeClient({ fetch, getPassword: () => "old", onUnauthorized })
    const error = await unwrap(client.GET("/api/today")).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  test("unwrap throws ApiError with the status on a 500", async () => {
    const { fetch } = fakeFetch(() => new Response("Internal Server Error", { status: 500 }))
    const client = makeClient({ fetch, getPassword: () => "pw", onUnauthorized: () => {} })
    await expect(unwrap(client.GET("/api/today"))).rejects.toMatchObject({ status: 500 })
  })

  test("unwrap returns the data on success", async () => {
    const { fetch } = fakeFetch(() => Response.json({ ok: true, version: "0.1.0" }))
    const client = makeClient({ fetch, getPassword: () => "pw", onUnauthorized: () => {} })
    await expect(unwrap(client.GET("/api/health"))).resolves.toEqual({ ok: true, version: "0.1.0" })
  })
})
