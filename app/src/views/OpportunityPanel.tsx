import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Star, TriangleAlert } from "lucide-react"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import type { Action } from "@/api/events"
import { keys } from "@/api/keys"
import { Deadline } from "@/components/Deadline"
import { KindPill } from "@/components/KindPill"
import { ScoreBar } from "@/components/ScoreBar"
import { useOnline } from "@/api/online"
import { Button, buttonVariants } from "@/components/ui/button"
import { safeUrl } from "@/lib/links"
import { cn } from "@/lib/utils"

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 text-sm">
      <span className="text-[11px] text-mist-2">{label}</span>
      <span className="text-mist">{children}</span>
    </div>
  )
}

/** The details of one opportunity, on the right. Esc (on the page) closes it. */
export function OpportunityPanel({ id, now, onAct }: { id: number; now: Date; onAct: (action: Action) => void }) {
  const online = useOnline()
  const { client } = useAuth()
  const { data: item, isError } = useQuery({
    queryKey: keys.opportunity(id),
    queryFn: () => unwrap(client.GET("/api/opportunities/{opportunity_id}", { params: { path: { opportunity_id: id } } })),
  })
  if (isError) return <aside className="panel-in w-[360px] shrink-0 border-l border-line bg-panel p-5 text-sm text-mist-2">This opportunity is gone.</aside>
  if (!item) return <aside className="panel-in w-[360px] shrink-0 border-l border-line bg-panel p-5 text-sm text-mist-2">Loading…</aside>
  const link = safeUrl(item.url)
  const where = [item.organizer, item.location].filter(Boolean).join(" · ")
  return (
    <aside aria-label={item.title} className="panel-in flex w-[360px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-line bg-panel p-5">
      <div className="flex items-center justify-between">
        <KindPill kind={item.kind} />
        <kbd className="rounded border border-line px-1.5 text-[11px] text-mist-2">Esc</kbd>
      </div>
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold leading-snug text-mist">{item.title}</h2>
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-mist-2">
          {where && <span>{where}</span>}
          <Deadline deadline={item.deadline} now={now} />
        </p>
      </div>
      {item.flagged && (
        <p data-severity="alert" className="flex items-center gap-1.5 text-sm text-alert">
          <TriangleAlert className="size-4" aria-hidden />This email tried to give the assistant instructions.
        </p>
      )}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <ScoreBar score={item.score} big />
          <span className="text-xs text-mist-2">Why it scored {item.score}</span>
        </div>
        {item.breakdown.map((part) => (
          <div key={part.label} className="flex justify-between border-t border-line pt-1.5 text-xs text-mist-2">
            <span>{part.label}</span>
            <span className="font-semibold text-mist tabular-nums">{part.points > 0 ? `+${part.points}` : part.points}</span>
          </div>
        ))}
      </div>
      {item.eligibility && <Field label="Eligibility">{item.eligibility}</Field>}
      {item.prize_or_pay && <Field label="Prize or pay">{item.prize_or_pay}</Field>}
      <div className="flex flex-wrap gap-1.5">
        {link && (
          <a href={link} target="_blank" rel="noreferrer noopener" className={cn(buttonVariants({ size: "sm" }))}>
            <ExternalLink aria-hidden />Open link
          </a>
        )}
        <Button size="sm" variant="outline" disabled={!online} onClick={() => onAct(item.starred ? "unstar" : "star")}>
          <Star aria-hidden />{item.starred ? "Unstar" : "Star"}
        </Button>
        <Button size="sm" variant="outline" disabled={!online} onClick={() => onAct(item.status === "dismissed" ? "restore" : "dismiss")}>
          {item.status === "dismissed" ? "Restore" : "Dismiss"}
        </Button>
        {item.status !== "applied" && <Button size="sm" variant="outline" disabled={!online} onClick={() => onAct("applied")}>Applied</Button>}
      </div>
      <div className="flex flex-col gap-2">
        <span className="text-[11px] text-mist-2">Seen in {item.sightings.length} {item.sightings.length === 1 ? "email" : "emails"}</span>
        {item.sightings.map((s) => {
          const gmail = safeUrl(s.gmail_url)
          return (
            <div key={s.message_id} className="flex items-center justify-between gap-2 border-t border-line pt-1.5 text-xs">
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-mist" title={s.subject ?? ""}>{s.subject ?? "(no subject)"}</span>
                <span className="truncate text-mist-2">{s.sender_name}</span>
              </span>
              {gmail && <a href={gmail} target="_blank" rel="noreferrer noopener" className="lamp-focus shrink-0 rounded text-mist underline-offset-2 hover:underline">Open in Gmail</a>}
            </div>
          )
        })}
      </div>
    </aside>
  )
}
