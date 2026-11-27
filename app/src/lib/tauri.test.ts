import { QueryClient, QueryObserver } from "@tanstack/react-query"
import { waitFor } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { inTheApp } from "../test/tauri"
import { focusFollowsTheWindow, installExternalLinks, isTauri, listen } from "./tauri"

afterEach(() => {
  delete (window as { __TAURI__?: unknown }).__TAURI__
  document.body.innerHTML = ""
})

test("outside the desktop app there is no Tauri", () => {
  expect(isTauri()).toBe(false)
})

test("in the app, a link that opens a new tab goes to the default browser", () => {
  const invoke = vi.fn(() => Promise.resolve())
  inTheApp(invoke)
  const remove = installExternalLinks()
  document.body.innerHTML = '<a target="_blank" href="https://example.com">out</a><a href="#/today">in</a>'
  const outside = new MouseEvent("click", { bubbles: true, cancelable: true })
  document.querySelector("a[target]")!.dispatchEvent(outside)
  expect(outside.defaultPrevented).toBe(true)
  expect(invoke).toHaveBeenCalledWith("plugin:opener|open_url", { url: "https://example.com/" })
  const inside = new MouseEvent("click", { bubbles: true, cancelable: true })
  document.querySelector('a[href="#/today"]')!.dispatchEvent(inside)
  expect(inside.defaultPrevented).toBe(false) // in-app links stay in the app
  expect(invoke).toHaveBeenCalledTimes(1)
  remove()
})

test("in the app, opening the window again refreshes what the dashboard shows", async () => {
  // a hidden app window still counts as visible to the page, so only the window's focus can tell
  focusFollowsTheWindow()
  const client = new QueryClient()
  client.mount()
  const fetches = vi.fn(() => Promise.resolve(1))
  const observer = new QueryObserver(client, { queryKey: ["today"], queryFn: fetches, staleTime: 0 })
  const unsubscribe = observer.subscribe(() => {})
  await waitFor(() => expect(fetches).toHaveBeenCalledTimes(1))
  window.dispatchEvent(new Event("blur")) // closed to the tray
  window.dispatchEvent(new Event("focus")) // opened from the tray
  await waitFor(() => expect(fetches).toHaveBeenCalledTimes(2))
  unsubscribe()
  client.unmount()
})

test("in the app, an event the app sends reaches the listener until it is removed", async () => {
  const { emit } = inTheApp(vi.fn())
  const handler = vi.fn()
  const stop = listen("connection-forgotten", handler)
  emit("connection-forgotten")
  expect(handler).toHaveBeenCalledTimes(1)
  stop()
  expect(window.__TAURI__!.event.listen).toHaveBeenCalledWith("connection-forgotten", handler)
})

test("outside the app, listening does nothing", () => {
  expect(() => listen("connection-forgotten", vi.fn())()).not.toThrow()
})
