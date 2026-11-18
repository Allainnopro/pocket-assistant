import { useQuery } from "@tanstack/react-query"
import { KindDot } from "@/components/KindDot"
import { KIND_INFO } from "@/lib/emailKinds"
import { ExternalLink } from "lucide-react"
import { Link } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { EmailBadges } from "@/components/EmailBadges"
import { KindPill } from "@/components/KindPill"
import { ScoreBar } from "@/components/ScoreBar"
import { buttonVariants } from "@/components/ui/button"
import { safeUrl } from "@/lib/links"
import { clock, dayGroup } from "@/lib/time"

/** One email: Gmail's excerpt, the badges, the action note, what was found in it. The body is never stored. */
export function EmailPane({ id }: { id: string }) {
  const { client } = useAuth()
  const { data: mail, isError } = useQuery({
    queryKey: keys.email(id),
    queryFn: () => unwrap(client.GET("/api/emails/{message_id}", { params: { path: { message_id: id } } })),
  })
  if (isError) return <aside className="panel-in w-[380px] shrink-0 border-l border-line bg-panel p-5 text-sm text-mist-2">This email isn't stored.</aside>
  if (!mail) return <aside className="panel-in w-[380px] shrink-0 border-l border-line bg-panel p-5 text-sm text-mist-2">Loading…</aside>
  const gmail = safeUrl(mail.gmail_url)
  return (
    <aside aria-label={mail.subject} className="panel-in flex w-[380px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-line bg-panel p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-mist-2">{dayGroup(mail.received_at, new Date())} · {clock(mail.received_at)}</span>
        <kbd className="rounded border border-line px-1.5 text-[11px] text-mist-2">Esc</kbd>
      </div>
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold leading-snug text-mist">{mail.subject}</h2>
        <span className="text-sm text-mist-2">{mail.sender_name}</span>
        <span className="flex items-center gap-1.5 text-xs text-mist-2"><KindDot kind={mail.kind} urgent={mail.urgent} />{KIND_INFO[mail.kind].label}</span>
      </div>
      <EmailBadges badges={mail.badges} />
      {mail.action_note && (
        <div className="flex flex-col gap-0.5 text-sm">
          <span className="text-[11px] text-mist-2">What it asks</span>
          <span className="text-mist">{mail.action_note}</span>
        </div>
      )}
      <p className="text-sm leading-relaxed text-mist-2">{mail.excerpt}</p>
      {mail.opportunities.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-mist-2">Found in this email</span>
          {mail.opportunities.map((item) => (
            <Link key={item.id} to={`/opportunities?tab=all&id=${item.id}`}
                  className="lamp-focus flex items-center gap-2 rounded-md border-t border-line px-1 py-1.5 hover:bg-hover">
              <ScoreBar score={item.score} />
              <KindPill kind={item.kind} />
              <span className="truncate text-sm text-mist" title={item.title}>{item.title}</span>
            </Link>
          ))}
        </div>
      )}
      {gmail && (
        <a href={gmail} target="_blank" rel="noreferrer noopener" className={buttonVariants({ size: "sm", className: "self-start" })}>
          <ExternalLink aria-hidden />Open in Gmail
        </a>
      )}
    </aside>
  )
}
