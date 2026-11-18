import { useQuery } from "@tanstack/react-query"
import { Bar, BarChart, Rectangle, XAxis, type BarShapeProps } from "recharts"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { KINDS, kindOf, type Kind } from "@/lib/kinds"
import { step } from "@/lib/motion"
import { dateLabel, fillDays, type SpendDay } from "@/lib/series"
import { hcmcDate } from "@/lib/time"
import { PALETTE } from "@/tokens"

const money = (usd: number) => `$${usd.toFixed(2)}`
const count = (n: number) => n.toLocaleString("en-US")
const card = "arrive flex flex-col gap-3 rounded-xl border border-line bg-panel px-5 py-4"
const heading = "text-[11px] font-semibold tracking-wider text-mist-2 uppercase"

function WeekTable({ caption, columns, label, weeks, pick }: {
  caption: string
  columns: string[]
  label: (column: string) => string
  weeks: { week_start: string }[]
  pick: (week: never, column: string) => number
}) {
  return (
    <table aria-label={caption} className="w-full text-sm tabular-nums">
      <thead>
        <tr className="text-left text-xs text-mist-2">
          <th scope="col" className="py-1.5 font-normal">Week</th>
          {columns.map((c) => <th key={c} scope="col" className="py-1.5 text-right font-normal">{label(c)}</th>)}
        </tr>
      </thead>
      <tbody>
        {weeks.map((week) => (
          <tr key={week.week_start} className="border-t border-line">
            <th scope="row" className="py-1.5 text-left font-normal text-mist">{dateLabel(week.week_start)}</th>
            {columns.map((c) => <td key={c} className="py-1.5 text-right text-mist">{pick(week as never, c)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function Reports() {
  const { client } = useAuth()
  const spend = useQuery({ queryKey: keys.spend(30), queryFn: () => unwrap(client.GET("/api/reports/spend", { params: { query: { days: 30 } } })) })
  const digests = useQuery({ queryKey: keys.digests, queryFn: () => unwrap(client.GET("/api/reports/digests")) })
  const items = useQuery({ queryKey: keys.items(8), queryFn: () => unwrap(client.GET("/api/reports/items", { params: { query: { weeks: 8 } } })) })
  const today = hcmcDate(new Date())

  const weeks = items.data?.weeks ?? []
  const kinds = (Object.keys(KINDS) as Kind[]).filter((k) => weeks.some((w) => w.by_kind[k]))
  const sources = [...new Set(weeks.flatMap((w) => Object.keys(w.by_source)))].sort()

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-8 py-7">
      <h1 className="arrive font-serif text-2xl font-normal text-mist" style={step(0)}>Reports</h1>

      {spend.data && (() => {
        const { month_usd, monthly_soft_cap_usd } = spend.data
        const used = monthly_soft_cap_usd > 0 ? Math.min(100, Math.round((month_usd / monthly_soft_cap_usd) * 100)) : 0
        const series = fillDays(spend.data.days, today, 30)
        const tokensIn = spend.data.days.reduce((sum, d) => sum + d.input_tokens, 0)
        const tokensOut = spend.data.days.reduce((sum, d) => sum + d.output_tokens, 0)
        return (
          <section className={card} style={step(1)}>
            <h2 className={heading}>Spend</h2>
            <p className="font-serif text-2xl text-mist">{money(month_usd)} of {money(monthly_soft_cap_usd)} this month</p>
            <div role="progressbar" aria-label="Monthly budget used" aria-valuenow={used} aria-valuemin={0} aria-valuemax={100}
                 className="h-1.5 overflow-hidden rounded-full bg-line">
              <div className="h-full rounded-full bg-mist" style={{ width: `${used}%` }} />
            </div>
            <BarChart data={series} responsive width="100%" height={140} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
              <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval={4} tickLine={false} axisLine={false}
                     tick={{ fill: PALETTE.mist2, fontSize: 11 }} />
              <Bar dataKey="cost_usd" isAnimationActive={false} radius={[2, 2, 0, 0]}
                   shape={(props: BarShapeProps) => (
                     <Rectangle {...props} fill={(props.payload as SpendDay).day === today ? PALETTE.mist : PALETTE.mist2} />
                   )} />
            </BarChart>
            <p className="text-xs text-mist-2">{count(tokensIn)} tokens in · {count(tokensOut)} out</p>
          </section>
        )
      })()}

      <section className={card} style={step(2)}>
        <h2 className={heading}>Digests</h2>
        {digests.data && digests.data.digests.length === 0 && <p className="text-sm text-mist-2">No digests yet. They start with Telegram.</p>}
        {digests.data?.digests.map((d) => (
          <div key={d.digest_date} data-digest className="flex items-center justify-between border-t border-line pt-1.5 text-sm">
            <span className="text-mist tabular-nums">{d.digest_date}</span>
            <span className="text-mist-2">{d.item_count} items</span>
            <span className="text-mist-2">{d.sent_at ? "Sent" : d.send_error ? `Not sent: ${d.send_error}` : "Not sent yet"}</span>
          </div>
        ))}
      </section>

      <section className={card} style={step(3)}>
        <h2 className={heading}>Items per week</h2>
        {weeks.length > 0 && (
          <>
            <WeekTable caption="Items per kind each week" columns={kinds} label={(k) => KINDS[kindOf(k)].label} weeks={weeks}
                       pick={(w: { by_kind: Record<string, number> }, k) => w.by_kind[k] ?? 0} />
            <WeekTable caption="Items per source each week" columns={sources} label={(s) => s} weeks={weeks}
                       pick={(w: { by_source: Record<string, number> }, s) => w.by_source[s] ?? 0} />
          </>
        )}
      </section>
    </div>
  )
}
