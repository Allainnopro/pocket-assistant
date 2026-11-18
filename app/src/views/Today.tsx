import { useQuery } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { useAct } from "@/api/useAct"
import { Deadline } from "@/components/Deadline"
import { EmptyState, LoadError } from "@/components/EmptyState"
import { KindDot } from "@/components/KindDot"
import { OpportunityItem } from "@/components/OpportunityItem"
import { StatusLine } from "@/components/StatusLine"
import { ROW_CAP, rowStep, step, useCountUp } from "@/lib/motion"
import { clock, greeting } from "@/lib/time"

const CARDS = [
  { key: "new_today", label: "New today", to: "/opportunities?tab=all&new=1" },
  { key: "deadlines_this_week", label: "Deadlines this week", to: "/opportunities?tab=all&within=6" },
  { key: "starred", label: "Starred", to: "/opportunities?tab=starred" },
  { key: "applied", label: "Applied", to: "/opportunities?tab=applied" },
] as const

const money = (usd: number) => `$${usd.toFixed(2)}`
const SHOW_EMAILS = 5 // a calm first screen: the rest are one click away
const SHOW_DEADLINES = 6

function CountUp({ value }: { value: number }) {
  return <>{useCountUp(value)}</>
}

function summary(n: number): string {
  if (n === 0) return "All caught up."
  return n === 1 ? "1 thing needs you today." : `${n} things need you today.`
}

export function Today() {
  const { client } = useAuth()
  const act = useAct()
  const navigate = useNavigate()
  const { data, isError, refetch } = useQuery({ queryKey: keys.today, queryFn: () => unwrap(client.GET("/api/today")) })
  if (!data) return isError ? <LoadError retry={refetch} /> : null
  const now = new Date()
  const waiting = data.top_picks.length + data.needs_action.length
  const problems = { ...data.status.problems } as Record<string, Record<string, unknown>>
  if (data.status.last_check_error && !problems.gmail) problems.gmail = { problem: data.status.last_check_error }

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto px-8 py-7">
      <header className="flex flex-col gap-1">
        <h1 className="arrive font-serif text-3xl font-normal text-mist" style={step(0)}>{greeting(now)}</h1>
        <p className="arrive text-sm text-mist-2" style={step(0)}>{summary(waiting)}</p>
      </header>

      <div className="grid grid-cols-4 gap-3">
        {CARDS.map(({ key, label, to }, i) => (
          <Link key={key} to={to} style={step(1 + i)}
                className="arrive lamp-hover lamp-focus flex flex-col gap-1 rounded-xl border border-line bg-panel px-4 py-3 transition-[scale,translate] duration-200 motion-safe:hover:-translate-y-0.5 motion-safe:hover:scale-[1.03]">
            <span className="text-xs text-mist-2">{label}</span>
            <span className="font-serif text-3xl text-mist tabular-nums"><CountUp value={data.counts[key]} /></span>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_17rem] gap-4">
        <section className="arrive flex flex-col rounded-xl border border-line bg-panel px-4 py-3" style={step(5)}>
          <h2 className="mb-1 text-[11px] font-semibold tracking-wider text-mist-2 uppercase">For you today</h2>
          {waiting === 0 && <EmptyState line="Nothing needs you today." />}
          {data.top_picks.map((item, i) => (
            <OpportunityItem key={item.id} item={item} now={now} top={i === 0} selected={false} style={rowStep(6, i)}
                             onOpen={() => navigate(`/opportunities?tab=all&id=${item.id}`)} onAct={(action) => act(item.id, action)} />
          ))}
          {data.needs_action.slice(0, SHOW_EMAILS).map((email, i) => (
            <Link key={email.message_id} to={`/emails?id=${encodeURIComponent(email.message_id)}`} style={rowStep(6, data.top_picks.length + i)}
                  className="arrive lift lamp-focus flex items-center gap-3 rounded-md border-t border-line px-2 py-2">
              <KindDot kind={email.kind} urgent={email.urgent} />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-medium text-mist" title={email.subject ?? ""}>{email.subject ?? "(no subject)"}</span>
                {email.action_note && <span className="truncate text-xs text-mist-2">{email.action_note}</span>}
              </span>
              <span className="shrink-0 text-xs text-mist-2 tabular-nums">{clock(email.received_at)}</span>
            </Link>
          ))}
          {data.needs_action.length > SHOW_EMAILS && (
            <Link to="/emails" style={rowStep(6, ROW_CAP)} className="arrive lamp-focus rounded-md border-t border-line px-2 pt-2 text-xs text-mist-2 hover:text-mist">
              {data.needs_action.length - SHOW_EMAILS} more emails ask for action
            </Link>
          )}
        </section>

        <section className="arrive flex flex-col rounded-xl border border-line bg-panel px-4 py-3" style={step(5)}>
          <h2 className="mb-1 text-[11px] font-semibold tracking-wider text-mist-2 uppercase">Upcoming deadlines</h2>
          {data.upcoming_deadlines.length === 0 && <p className="py-6 text-center text-sm text-mist-2">No deadlines in the next 7 days.</p>}
          {data.upcoming_deadlines.slice(0, SHOW_DEADLINES).map((item, i) => (
            <Link key={item.id} to={`/opportunities?tab=all&id=${item.id}`} style={rowStep(6, i)}
                  className="arrive lift lamp-focus flex flex-col gap-0.5 rounded-md border-t border-line px-1 py-1.5">
              <Deadline deadline={item.deadline} now={now} />
              <span className="truncate text-sm text-mist" title={item.title}>{item.title}</span>
            </Link>
          ))}
          {data.upcoming_deadlines.length > SHOW_DEADLINES && (
            <Link to="/opportunities?tab=all&within=6" style={rowStep(6, ROW_CAP)} className="arrive lamp-focus rounded-md border-t border-line px-1 pt-2 text-xs text-mist-2 hover:text-mist">
              {data.upcoming_deadlines.length - SHOW_DEADLINES} more this week
            </Link>
          )}
        </section>
      </div>

      <footer className="arrive mt-auto border-t border-line pt-3" style={step(7 + ROW_CAP)}>
        <StatusLine lastCheck={data.status.last_ok_check_at} problems={problems}
                    spend={`${money(data.spend.month_usd)} of ${money(data.spend.monthly_soft_cap_usd)} this month`} />
      </footer>
    </div>
  )
}
