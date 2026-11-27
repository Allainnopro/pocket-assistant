import type { Mock } from "vitest"
import { vi } from "vitest"

type Invoke = NonNullable<Window["__TAURI__"]>["core"]["invoke"]
type Listen = NonNullable<Window["__TAURI__"]>["event"]["listen"]

/** Pretend the dashboard runs inside the desktop app, with `invoke` standing in for its commands.
 * `emit(name)` plays an event the app sends to the window (like `connection-forgotten`). */
export function inTheApp(invoke: Mock) {
  const handlers = new Map<string, Array<() => void>>()
  const listen = vi.fn((name: string, handler: () => void) => {
    handlers.set(name, [...(handlers.get(name) ?? []), handler])
    return Promise.resolve(() => {})
  })
  window.__TAURI__ = { core: { invoke: invoke as unknown as Invoke }, event: { listen: listen as unknown as Listen } }
  return { emit: (name: string) => handlers.get(name)?.forEach((handler) => handler()) }
}
