import { KINDS, kindOf } from "@/lib/kinds"

export function KindPill({ kind }: { kind: string }) {
  const { label, Icon } = KINDS[kindOf(kind)]
  return (
    <span data-kind={kind}
          className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line px-2 py-px text-[11px] text-mist-2">
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  )
}
