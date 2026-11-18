import { useQueries, useQuery } from "@tanstack/react-query"
import { X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import type { Action } from "@/api/events"
import { keys } from "@/api/keys"
import { useAct } from "@/api/useAct"
import { EmptyState, LoadError } from "@/components/EmptyState"
import { OpportunityItem } from "@/components/OpportunityItem"
import { KINDS, type Kind } from "@/lib/kinds"
import { useShortcuts } from "@/lib/keys"
import { rowStep, step } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { OpportunityPanel } from "./OpportunityPanel"

const TABS = [
  { id: "for_you", label: "For you" }, { id: "all", label: "All" }, { id: "starred", label: "Starred" },
  { id: "applied", label: "Applied" }, { id: "dismissed", label: "Dismissed" },
] as const
type Tab = (typeof TABS)[number]["id"]
const FILTERS = ["kind", "within", "min", "q", "new"] as const
const select = "lamp-focus rounded-full border border-line bg-ink px-2.5 py-1 text-xs text-mist"

function useTabCounts() {
  const { client } = useAuth()
  const counts = useQueries({
    queries: TABS.map(({ id }) => ({
      queryKey: keys.tabCount(id),
      queryFn: () => unwrap(client.GET("/api/opportunities", { params: { query: { tab: id, limit: 1 } } })),
    })),
  })
  return Object.fromEntries(TABS.map(({ id }, i) => [id, counts[i].data?.tab_total]))
}

export function Opportunities() {
  const { client } = useAuth()
  const act = useAct()
  const [params, setParams] = useSearchParams()
  const tab = (params.get("tab") ?? "for_you") as Tab
  const filters = {
    kind: params.get("kind") ?? undefined,
    deadline_within: params.has("within") ? Number(params.get("within")) : undefined,
    min_score: params.has("min") ? Number(params.get("min")) : undefined,
    q: params.get("q") ?? undefined,
    new_today: params.get("new") === "1" ? true : undefined,
  }
  const filtering = FILTERS.some((name) => params.has(name))
  const list = useQuery({
    queryKey: keys.opportunities({ tab, ...filters }),
    queryFn: () => unwrap(client.GET("/api/opportunities", { params: { query: { tab, ...filters, limit: 1000 } } })),
  })
  const counts = useTabCounts()
  const items = list.data?.items ?? []
  const openId = params.has("id") ? Number(params.get("id")) : null
  const [selected, setSelected] = useState(-1)
  const [text, setText] = useState(filters.q ?? "")
  const now = new Date()

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

  const q = filters.q ?? ""
  const written = useRef<string | null>(null) // the q this box itself last put in the URL
  useEffect(() => { // q changed elsewhere (Ctrl+K, the sidebar, Back): the box follows; our own write mustn't eat later keys
    if (q !== written.current) setText(q)
    written.current = null
  }, [q])
  useEffect(() => { // the text filter waits for a pause in typing
    const timer = setTimeout(() => {
      if (text === q) return
      written.current = text
      update({ q: text })
    }, 200)
    return () => clearTimeout(timer)
  })
  useEffect(() => setSelected(-1), [tab])
  useEffect(() => { // j/k: keep the highlighted row on screen
    document.querySelector('[data-row][data-selected="true"]')?.scrollIntoView({ block: "nearest" })
  }, [selected])

  const current = selected >= 0 ? items[selected] : undefined
  function onAct(item: (typeof items)[number], action: Action) {
    act(item.id, action)
  }
  useShortcuts({
    j: () => setSelected((i) => Math.min(i + 1, items.length - 1)),
    k: () => setSelected((i) => Math.max(i - 1, 0)),
    Enter: () => current && update({ id: String(current.id) }),
    Escape: () => (openId !== null ? update({ id: null }) : setSelected(-1)),
    s: () => current && onAct(current, current.starred ? "unstar" : "star"),
    d: () => current && onAct(current, current.status === "dismissed" ? "restore" : "dismiss"),
    a: () => current && current.status !== "applied" && onAct(current, "applied"),
  })

  const hidden = list.data && filtering ? list.data.tab_total - list.data.total : 0
  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-5">
        <h1 className="arrive font-serif text-2xl font-normal text-mist" style={step(0)}>Opportunities</h1>
        <div role="tablist" className="arrive flex gap-5 border-b border-line" style={step(1)}>
          {TABS.map(({ id, label }) => (
            <button key={id} role="tab" type="button" aria-selected={id === tab}
                    onClick={() => update({ tab: id === "for_you" ? null : id, id: null })}
                    className={cn("lamp-focus -mb-px border-b-2 pb-2 text-sm",
                      id === tab ? "border-mist text-mist" : "border-transparent text-mist-2 hover:text-mist")}>
              {label} <span className="text-xs text-mist-2 tabular-nums">{counts[id] ?? ""}</span>
            </button>
          ))}
        </div>
        <div className="arrive flex flex-wrap items-center gap-2" style={step(2)}>
          <select aria-label="Kind" className={select} value={filters.kind ?? ""} onChange={(e) => update({ kind: e.target.value })}>
            <option value="">Kind: any</option>
            {(Object.keys(KINDS) as Kind[]).map((kind) => <option key={kind} value={kind}>{KINDS[kind].label}</option>)}
          </select>
          <select aria-label="Deadline" className={select} value={params.get("within") ?? ""} onChange={(e) => update({ within: e.target.value })}>
            <option value="">Deadline: any</option>
            {[3, 7, 14, 30].map((days) => <option key={days} value={days}>Within {days} days</option>)}
          </select>
          <select aria-label="Minimum score" className={select} value={params.get("min") ?? ""} onChange={(e) => update({ min: e.target.value })}>
            <option value="">Score: any</option>
            {[50, 60, 75].map((score) => <option key={score} value={score}>Score ≥ {score}</option>)}
          </select>
          {params.get("new") === "1" && (
            <button type="button" onClick={() => update({ new: null })} className={cn(select, "lamp-hover inline-flex items-center gap-1")}>
              New today <X className="size-3" aria-label="Remove" />
            </button>
          )}
          <input aria-label="Filter" maxLength={100} placeholder="Filter by title or organizer" value={text} onChange={(e) => setText(e.target.value)}
                 className="lamp-focus min-w-48 flex-1 rounded-full border border-line bg-ink px-3 py-1 text-xs text-mist placeholder:text-mist-2" />
          {hidden > 0 && (
            <span className="text-xs text-mist-2">
              <span>{hidden} hidden by filters</span> ·{" "}
              <button type="button" className="lamp-focus rounded text-mist underline-offset-2 hover:underline"
                      onClick={() => { setText(""); update({ kind: null, within: null, min: null, q: null, new: null }) }}>Clear</button>
            </span>
          )}
        </div>
        {list.isError && !list.data ? (
          <LoadError retry={list.refetch} />
        ) : list.isSuccess && items.length === 0 ? (
          <div className="arrive" style={step(3)}><EmptyState line={filtering ? "Nothing matches these filters." : "No opportunities here yet."} /></div>
        ) : (
          <div key={tab} className="flex flex-col">
            {items.map((item, i) => (
              <OpportunityItem key={item.id} item={item} now={now} top={tab === "for_you" && i === 0} selected={i === selected} style={rowStep(3, i)}
                               onOpen={() => update({ id: String(item.id) })} onAct={(action) => onAct(item, action)} />
            ))}
          </div>
        )}
      </section>
      {openId !== null && <OpportunityPanel key={openId} id={openId} now={now} onAct={(action) => act(openId, action)} />}
    </div>
  )
}
