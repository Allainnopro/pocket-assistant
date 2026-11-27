import type { CSSProperties } from "react"
import type { components } from "@/api/schema"
import type { Action } from "@/api/events"
import { useOnline } from "@/api/online"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Deadline } from "./Deadline"
import { KindPill } from "./KindPill"
import { ScoreBar } from "./ScoreBar"
import { TopPick } from "./TopPick"

type Row = components["schemas"]["OpportunityRow"]

/** One dense row: score, kind, title over "organizer · why", seen ×N, deadline; Dismiss and Applied on hover. */
export function OpportunityItem({ item, now, top, selected, onOpen, onAct, style }: {
  item: Row
  now: Date
  top: boolean
  selected: boolean
  onOpen: () => void
  onAct: (action: Action) => void
  style?: CSSProperties // its arrival level (lib/motion)
}) {
  const subtitle = [item.organizer, item.why].filter(Boolean).join(" · ")
  const online = useOnline()
  const dismissed = item.status === "dismissed"
  return (
    <div data-row={item.id} data-selected={selected} onClick={onOpen} style={style}
         className={cn("group arrive lift flex cursor-default items-center gap-2.5 rounded-md border-t border-line px-2 py-2",
           "data-[selected=true]:bg-hover data-[selected=true]:shadow-[inset_2px_0_0_var(--color-mist)]")}>
      <TopPick on={top} />
      <ScoreBar score={item.score} />
      <span className="w-32 shrink-0"><KindPill kind={item.kind} /></span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium text-mist" title={item.title}>{item.title}</span>
        <span className="truncate text-xs text-mist-2" title={subtitle}>{subtitle}</span>
      </div>
      {item.seen_count > 1 && <span className="shrink-0 text-[11px] text-mist-2">seen ×{item.seen_count}</span>}
      <Deadline deadline={item.deadline} now={now} />
      <div className={cn("flex w-40 shrink-0 justify-end gap-1.5 opacity-0 transition-opacity duration-150 group-focus-within:opacity-100 group-hover:opacity-100",
                         selected && "opacity-100")}
           onClick={(event) => event.stopPropagation()}>
        <Button size="xs" variant="outline" disabled={!online} onClick={() => onAct(dismissed ? "restore" : "dismiss")}>
          {dismissed ? "Restore" : "Dismiss"}
        </Button>
        {item.status !== "applied" && <Button size="xs" variant="outline" disabled={!online} onClick={() => onAct("applied")}>Applied</Button>}
      </div>
    </div>
  )
}
