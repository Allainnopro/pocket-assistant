import type { Mock } from "vitest"

type Invoke = NonNullable<Window["__TAURI__"]>["core"]["invoke"]

/** Pretend the dashboard runs inside the desktop app, with `invoke` standing in for its commands. */
export function inTheApp(invoke: Mock) {
  window.__TAURI__ = { core: { invoke: invoke as unknown as Invoke } }
}
