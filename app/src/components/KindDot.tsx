import { KIND_INFO, type EmailKind } from "@/lib/emailKinds"

/** An email's kind as a small dot: its kind's colour, red while urgent, a hollow ring when unsorted.
 * Named for screen readers and on hover; `hidden` when words beside it already say it (the legend). */
export function KindDot({ kind, urgent, hidden = false }: { kind: EmailKind; urgent: boolean; hidden?: boolean }) {
  const known = kind in KIND_INFO ? kind : "unsorted" // an engine of another version could send a kind we lack
  const { label, colour } = KIND_INFO[known]
  const name = urgent ? `Urgent · ${label}` : label
  const hollow = known === "unsorted" && !urgent
  return (
    <span role={hidden ? undefined : "img"} aria-label={hidden ? undefined : name} aria-hidden={hidden || undefined}
          title={hidden ? undefined : name} data-urgent={urgent} data-hollow={hollow}
          className="inline-block size-2 shrink-0 rounded-full"
          style={hollow ? { boxShadow: `inset 0 0 0 1.5px ${colour}` } : { backgroundColor: urgent ? "var(--alert)" : colour }} />
  )
}
