import { TriangleAlert } from "lucide-react"

/** Shown over the last data we loaded while the engine can't be reached. */
export function OfflineBanner({ since }: { since: string }) {
  return (
    <p role="status" className="flex items-center gap-1.5 border-b border-line px-4 py-1.5 text-xs text-alert">
      <TriangleAlert className="size-3.5" aria-hidden />
      Engine unreachable since {since} · retrying
    </p>
  )
}
