import { useQuery } from "@tanstack/react-query"
import { Fragment, useEffect, useState } from "react"
import { useSearchParams } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { EmailBadges } from "@/components/EmailBadges"
import { EmptyState, LoadError } from "@/components/EmptyState"
import { KindDot } from "@/components/KindDot"
import { KIND_INFO, KIND_ORDER } from "@/lib/emailKinds"
import { useShortcuts } from "@/lib/keys"
import { rowStep, step } from "@/lib/motion"
import { clock, dayGroup } from "@/lib/time"
import { cn } from "@/lib/utils"
import { EmailPane } from "./EmailPane"

const SECTIONS = [{ id: "important", label: "Important" }, { id: "other", label: "Other" }] as const
type Section = (typeof SECTIONS)[number]["id"]

export function Emails() {
  const { client } = useAuth()
  const [params, setParams] = useSearchParams()
  const section = (params.get("section") ?? "important") as Section
  const days = Number(params.get("days") ?? 7)
  const query = { section, days } // the same key as the sidebar's count when on the defaults
  const list = useQuery({
    queryKey: keys.emails(query),
    queryFn: () => unwrap(client.GET("/api/emails", { params: { query } })),
  })
  const items = list.data?.items ?? []
  const openId = params.get("id")
  const [selected, setSelected] = useState(-1)
  const now = new Date()
  useEffect(() => { // j/k: keep the highlighted row on screen
    document.querySelector('[data-row][data-selected="true"]')?.scrollIntoView({ block: "nearest" })
  }, [selected])

  function update(changes: Record<string, string | null>) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      for (const [name, value] of Object.entries(changes)) {
        if (value === null || value === "") next.delete(name)
        else next.set(name, value)
      }
      return next
    })
  }
  useEffect(() => setSelected(-1), [section, days])
  useShortcuts({
    j: () => setSelected((i) => Math.min(i + 1, items.length - 1)),
    k: () => setSelected((i) => Math.max(i - 1, 0)),
    Enter: () => selected >= 0 && items[selected] && update({ id: items[selected].message_id }),
    Escape: () => (openId !== null ? update({ id: null }) : setSelected(-1)),
  })

  let lastGroup = ""
  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-5">
        <h1 className="arrive font-serif text-2xl font-normal text-mist" style={step(0)}>Emails</h1>
        <div className="arrive flex items-end justify-between border-b border-line" style={step(1)}>
          <div role="tablist" className="flex gap-5">
            {SECTIONS.map(({ id, label }) => (
              <button key={id} role="tab" type="button" aria-selected={id === section}
                      onClick={() => update({ section: id === "important" ? null : id, id: null })}
                      className={cn("lamp-focus -mb-px border-b-2 pb-2 text-sm",
                        id === section ? "border-mist text-mist" : "border-transparent text-mist-2 hover:text-mist")}>
                {label}
              </button>
            ))}
          </div>
          <select aria-label="Days" value={days} onChange={(e) => update({ days: e.target.value === "7" ? null : e.target.value })}
                  className="lamp-focus mb-1.5 rounded-full border border-line bg-ink px-2.5 py-1 text-xs text-mist">
            {[7, 14, 30].map((n) => <option key={n} value={n}>Last {n} days</option>)}
          </select>
        </div>
        {items.length > 0 && (
          <ul aria-label="Kinds" className="arrive flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-mist-2" style={step(1)}>
            {KIND_ORDER.filter((kind) => items.some((mail) => mail.kind === kind)).map((kind) => (
              <li key={kind} className="flex items-center gap-1.5"><KindDot kind={kind} urgent={false} hidden />{KIND_INFO[kind].label}</li>
            ))}
          </ul>
        )}
        {list.data?.truncated && <p className="text-xs text-mist-2">Showing the newest 500 in this window.</p>}
        {list.isError && !list.data ? (
          <LoadError retry={list.refetch} />
        ) : list.isSuccess && items.length === 0 ? (
          <div className="arrive" style={step(2)}><EmptyState line={`No ${section} email in the last ${days} days.`} /></div>
        ) : (
          <div key={section} className="flex flex-col">
            {items.map((mail, i) => {
              const group = dayGroup(mail.received_at, now)
              const heading = group !== lastGroup
              lastGroup = group
              return (
                <Fragment key={mail.message_id}>
                  {heading && <h2 style={rowStep(2, i)} className="arrive mt-3 mb-1 px-2 text-[11px] font-semibold tracking-wider text-mist-2 uppercase">{group}</h2>}
                  <div data-row={mail.message_id} data-selected={i === selected} onClick={() => update({ id: mail.message_id })} style={rowStep(2, i)}
                       className="arrive lift flex cursor-default items-center gap-3 rounded-md border-t border-line px-2 py-2 data-[selected=true]:bg-hover data-[selected=true]:shadow-[inset_2px_0_0_var(--color-mist)]">
                    <KindDot kind={mail.kind} urgent={mail.urgent} />
                    <span className="w-36 shrink-0 truncate text-sm text-mist" title={mail.sender_name}>{mail.sender_name}</span>
                    <span className="flex min-w-0 flex-1 items-baseline gap-2">
                      <span className="shrink-0 truncate font-medium text-mist max-w-[50%]" title={mail.subject}>{mail.subject}</span>
                      <span className="truncate text-xs text-mist-2">{mail.excerpt}</span>
                    </span>
                    <EmailBadges badges={mail.badges} />
                    <span className="w-11 shrink-0 text-right text-xs text-mist-2 tabular-nums">{clock(mail.received_at)}</span>
                  </div>
                </Fragment>
              )
            })}
          </div>
        )}
      </section>
      {openId !== null && <EmailPane key={openId} id={openId} />}
    </div>
  )
}
