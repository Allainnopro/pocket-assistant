import type { QueryKey } from "@tanstack/react-query"

// The engine's live events (src/assistant/engine/events.py). OpenAPI can't describe the stream's
// items, so these are typed by hand. Events carry counts and states only, never mail content.
export type Action = "star" | "unstar" | "dismiss" | "restore" | "applied"
export type EngineEvent =
  | { kind: "items_new"; new: number; merged: number }
  | { kind: "item_changed"; id: number; action: Action }
  | { kind: "ranking_changed"; rescored: number }
  | { kind: "status"; source: string; problem: string | null; [detail: string]: unknown }

const KINDS = new Set(["items_new", "item_changed", "ranking_changed", "status"])
export const BACKOFF_MS = [1000, 2000, 5000, 10000] // then every 10 s

/** Server-sent events from a fetch body, by the WHATWG rules (the reader the research verified). */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = "", event = "", data = ""
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return // an event without its closing blank line is dropped
      buffer += decoder.decode(value, { stream: true })
      let match: RegExpExecArray | null
      while ((match = /\r\n|\n|\r(?!$)/.exec(buffer))) { // a lone CR at the end may be half of a CRLF
        const line = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        if (line === "") {
          if (data) yield { event: event || "message", data: data.slice(0, -1) }
          event = ""
          data = ""
          continue
        }
        if (line.startsWith(":")) continue // a comment, like FastAPI's ": ping"
        const colon = line.indexOf(":")
        const field = colon < 0 ? line : line.slice(0, colon)
        let value = colon < 0 ? "" : line.slice(colon + 1)
        if (value.startsWith(" ")) value = value.slice(1)
        if (field === "event") event = value
        else if (field === "data") data += value + "\n"
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function parseEvent(raw: { event: string; data: string }): EngineEvent | null {
  if (!KINDS.has(raw.event)) return null
  try {
    return { kind: raw.event, ...JSON.parse(raw.data) } as EngineEvent
  } catch {
    return null
  }
}

/** Which cached answers an event makes stale (keys.ts prefixes). */
export function invalidationFor(event: EngineEvent): QueryKey[] {
  switch (event.kind) {
    case "item_changed":
      return [["opportunities"], ["opportunity", event.id], ["tabCount"], ["today"]]
    case "items_new":
      return [["opportunities"], ["tabCount"], ["today"], ["emails"]]
    case "ranking_changed":
      return [["opportunities"], ["tabCount"], ["today"]]
    case "status":
      return [["today"], ["settings"]]
  }
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener("abort", () => {
      clearTimeout(timer)
      resolve()
    }, { once: true })
  })
}

/** Keeps a stream open for as long as `signal` allows. After a drop it reconnects with a backoff and
 * calls onReconnect, since events may have been missed meanwhile. A 401 ends it: the password changed. */
export function connectEvents(opts: {
  fetch?: typeof globalThis.fetch
  url: string
  getPassword: () => string | null
  onEvent: (event: EngineEvent) => void
  onReconnect: () => void
  onUnauthorized: () => void
  signal: AbortSignal
}): void {
  const doFetch = opts.fetch ?? globalThis.fetch
  void (async () => {
    let failures = 0
    let connectedBefore = false
    while (!opts.signal.aborted) {
      try {
        const password = opts.getPassword()
        const response = await doFetch(opts.url, {
          headers: password ? { Authorization: `Bearer ${password}` } : {},
          signal: opts.signal,
        })
        if (response.status === 401) {
          opts.onUnauthorized()
          return
        }
        if (response.ok && response.body) {
          failures = 0
          if (connectedBefore) opts.onReconnect()
          connectedBefore = true
          for await (const raw of readSse(response.body)) {
            const event = parseEvent(raw)
            if (event) opts.onEvent(event)
          }
        }
      } catch {
        // offline, engine stopped, or aborted: the loop decides
      }
      if (opts.signal.aborted) return
      await pause(BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)], opts.signal)
      failures += 1
    }
  })()
}
