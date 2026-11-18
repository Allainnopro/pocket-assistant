import { afterEach, describe, expect, test, vi } from "vitest"
import { fakeFetch } from "../test/fakeFetch"
import { connectEvents, invalidationFor, parseEvent, readSse } from "./events"

const encoder = new TextEncoder()

/** A body that sends these chunks; `open` keeps it from ending (a live stream). */
function streamOf(chunks: (string | Uint8Array)[], open = false) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk)
      if (!open) controller.close()
    },
  })
}

async function collect(body: ReadableStream<Uint8Array>) {
  const out = []
  for await (const event of readSse(body)) out.push(event)
  return out
}

describe("readSse", () => {
  test("reads events split across chunks", async () => {
    const events = await collect(streamOf(["event: items_new\nda", 'ta: {"new": 2, "merged": 0}\n', "\n"]))
    expect(events).toEqual([{ event: "items_new", data: '{"new": 2, "merged": 0}' }])
  })

  test("a CRLF split across chunks is one line break", async () => {
    const events = await collect(streamOf(["event: status\r", "\ndata: {}\r\n\r\n"]))
    expect(events).toEqual([{ event: "status", data: "{}" }])
  })

  test("ping comments are ignored", async () => {
    const events = await collect(streamOf([": ping\n\n", "event: ranking_changed\ndata: {\"rescored\": 3}\n\n"]))
    expect(events).toEqual([{ event: "ranking_changed", data: '{"rescored": 3}' }])
  })

  test("an event cut off by the end of the stream is dropped", async () => {
    expect(await collect(streamOf(["event: items_new\ndata: {}\n"]))).toEqual([])
  })
})

describe("parseEvent", () => {
  test("known kinds become typed events", () => {
    expect(parseEvent({ event: "item_changed", data: '{"id": 7, "action": "star"}' }))
      .toEqual({ kind: "item_changed", id: 7, action: "star" })
  })

  test("unknown kinds and bad JSON are dropped", () => {
    expect(parseEvent({ event: "something_new", data: "{}" })).toBeNull()
    expect(parseEvent({ event: "items_new", data: "{not json" })).toBeNull()
  })
})

describe("invalidationFor", () => {
  test("item_changed refreshes that item, the lists, the counts and Today", () => {
    expect(invalidationFor({ kind: "item_changed", id: 7, action: "star" }))
      .toEqual([["opportunities"], ["opportunity", 7], ["tabCount"], ["today"]])
  })

  test("new items also refresh the emails; a status change refreshes Today and Settings", () => {
    expect(invalidationFor({ kind: "items_new", new: 2, merged: 0 }))
      .toEqual([["opportunities"], ["tabCount"], ["today"], ["emails"]])
    expect(invalidationFor({ kind: "status", source: "gmail", problem: null })).toEqual([["today"], ["settings"]])
  })
})

describe("connectEvents", () => {
  afterEach(() => vi.useRealTimers())

  test("after the stream drops it reconnects and refetches everything", async () => {
    // Review Focus 2: the PC slept, or Wi-Fi dropped
    vi.useFakeTimers()
    let call = 0
    const { fetch, calls } = fakeFetch(() => {
      call += 1
      const body = call === 1 ? streamOf(['event: items_new\ndata: {"new": 1, "merged": 0}\n\n']) : streamOf([], true)
      return new Response(body, { headers: { "content-type": "text/event-stream" } })
    })
    const onEvent = vi.fn()
    const onReconnect = vi.fn()
    const controller = new AbortController()
    connectEvents({ fetch, url: "http://127.0.0.1:47615/api/events", getPassword: () => "pw", onEvent, onReconnect,
                    onUnauthorized: () => {}, signal: controller.signal })
    await vi.advanceTimersByTimeAsync(10)
    expect(onEvent).toHaveBeenCalledWith({ kind: "items_new", new: 1, merged: 0 })
    expect(calls).toHaveLength(1)
    expect(calls[0].headers.get("Authorization")).toBe("Bearer pw")
    await vi.advanceTimersByTimeAsync(980) // t ≈ 990 ms: the stream ended at once, so the retry is due at 1 s
    expect(calls).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(30) // t ≈ 1020 ms
    expect(calls).toHaveLength(2)
    expect(onReconnect).toHaveBeenCalledTimes(1)
    controller.abort()
  })

  test("it backs off while the engine stays down", async () => {
    vi.useFakeTimers()
    const { fetch, calls } = fakeFetch(() => {
      throw new TypeError("Failed to fetch")
    })
    const controller = new AbortController()
    connectEvents({ fetch, url: "http://x/api/events", getPassword: () => "pw", onEvent: () => {}, onReconnect: () => {},
                    onUnauthorized: () => {}, signal: controller.signal })
    await vi.advanceTimersByTimeAsync(10)
    expect(calls).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1000) // 1 s
    expect(calls).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(2000) // 2 s
    expect(calls).toHaveLength(3)
    await vi.advanceTimersByTimeAsync(5000) // 5 s
    expect(calls).toHaveLength(4)
    await vi.advanceTimersByTimeAsync(9000)
    expect(calls).toHaveLength(4) // then every 10 s
    await vi.advanceTimersByTimeAsync(1000)
    expect(calls).toHaveLength(5)
    controller.abort()
  })

  test("a 401 on the stream signs out and stops reconnecting", async () => {
    vi.useFakeTimers()
    const { fetch, calls } = fakeFetch(() => Response.json({ detail: "no" }, { status: 401 }))
    const onUnauthorized = vi.fn()
    connectEvents({ fetch, url: "http://x/api/events", getPassword: () => "old", onEvent: () => {}, onReconnect: () => {},
                    onUnauthorized, signal: new AbortController().signal })
    await vi.advanceTimersByTimeAsync(30_000)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(1)
  })
})
