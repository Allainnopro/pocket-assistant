import { focusManager } from "@tanstack/react-query"

/** The desktop app (Tauri, `withGlobalTauri`): present only when the dashboard runs inside it. */
type TauriGlobal = {
  core: { invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> }
  event: { listen: (name: string, handler: () => void) => Promise<() => void> }
}

declare global {
  interface Window {
    __TAURI__?: TauriGlobal
  }
}

/** What the app remembers about the engine: its base URL and this computer's device key (app/src-tauri). */
export type Connection = { baseUrl: string; key: string }

export function isTauri(): boolean {
  return typeof window !== "undefined" && typeof window.__TAURI__?.core?.invoke === "function"
}

export function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__
  return tauri ? tauri.core.invoke<T>(cmd, args) : Promise.reject(new Error("not in the desktop app"))
}

/** Run `handler` whenever the app sends the window `name`; returns a function that stops listening. */
export function listen(name: string, handler: () => void): () => void {
  const pending = window.__TAURI__?.event?.listen(name, handler)
  return () => void pending?.then((unlisten) => unlisten())
}

/** In the app the page stays "visible" while its window is hidden in the tray, so react-query's refetch on
 * refocus (it listens to visibilitychange) follows the window's own focus instead. */
export function focusFollowsTheWindow(): void {
  focusManager.setEventListener((setFocused) => {
    const focus = () => setFocused(true)
    const blur = () => setFocused(false)
    window.addEventListener("focus", focus)
    window.addEventListener("blur", blur)
    return () => {
      window.removeEventListener("focus", focus)
      window.removeEventListener("blur", blur)
    }
  })
}

/** In the app, a link meant for a new tab (Gmail, an opportunity's page) opens in the default browser. */
export function installExternalLinks(): () => void {
  function onClick(event: MouseEvent) {
    const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[target=_blank]") : null
    if (!link || !/^https?:/i.test(link.href)) return
    event.preventDefault()
    void invoke("plugin:opener|open_url", { url: link.href })
  }
  document.addEventListener("click", onClick)
  return () => document.removeEventListener("click", onClick)
}
