import { cn } from "@/lib/utils"

/** The single best item gets the lamp dot; the others keep the same space so rows line up. */
export function TopPick({ on }: { on: boolean }) {
  return (
    <span aria-label={on ? "Top pick" : undefined} role={on ? "img" : undefined}
          className={cn("inline-block size-1.5 shrink-0 rounded-full", on ? "bg-lamp" : "bg-transparent")} />
  )
}
