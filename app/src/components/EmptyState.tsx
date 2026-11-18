import { Moon } from "lucide-react"
import { Button } from "@/components/ui/button"

/** Where the chibi will sit (M5). Until then, a quiet placeholder and one line. */
export function EmptyState({ line }: { line: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center text-mist-2">
      <span className="grid size-12 place-items-center rounded-full border border-line" aria-hidden>
        <Moon className="size-5" />
      </span>
      <p className="text-sm">{line}</p>
    </div>
  )
}

/** A first load that failed: one line and a way to try again, never a blank screen. */
export function LoadError({ retry }: { retry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-12 text-center text-sm text-mist-2">
      <p>Couldn't load this from the engine.</p>
      <Button variant="outline" size="sm" onClick={() => retry()}>Try again</Button>
    </div>
  )
}
