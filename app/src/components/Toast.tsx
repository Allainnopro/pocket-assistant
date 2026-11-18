import { TriangleAlert } from "lucide-react"
import { useSyncExternalStore } from "react"
import { cn } from "@/lib/utils"

export const TOAST_MS = 4000

type Toast = { id: number; message: string; kind: "done" | "alert" }
let toasts: Toast[] = []
let next = 0
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

/** A short note after an action: "done" with a brief lamp glow, "alert" in soft red with an icon. */
export function toast(message: string, kind: "done" | "alert" = "done") {
  const item = { id: ++next, message, kind }
  toasts = [...toasts, item]
  emit()
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== item.id)
    emit()
  }, TOAST_MS)
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function Toaster() {
  const list = useSyncExternalStore(subscribe, () => toasts)
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex flex-col gap-2">
      {list.map((t) => (
        <div key={t.id} role={t.kind === "alert" ? "alert" : "status"}
             className={cn("flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-sm",
               "animate-in fade-in slide-in-from-bottom-2 duration-300 motion-reduce:animate-none",
               t.kind === "done" ? "text-mist shadow-[0_0_0_3px_var(--lamp-glow)]" : "text-alert")}>
          {t.kind === "alert" && <TriangleAlert className="size-4" aria-hidden />}
          {t.message}
        </div>
      ))}
    </div>
  )
}
