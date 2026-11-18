import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"

const PAGES: Record<string, string> = { today: "/", opportunities: "/opportunities", emails: "/emails", reports: "/reports", settings: "/settings" }
const GROUPS = [
  { type: "opportunity", heading: "Opportunities", label: "Opportunity" },
  { type: "email", heading: "Emails", label: "Email" },
  { type: "page", heading: "Pages", label: "Page" },
] as const

function destination(type: string, id: string): string {
  if (type === "opportunity") return `/opportunities?tab=all&id=${encodeURIComponent(id)}`
  if (type === "email") return `/emails?id=${encodeURIComponent(id)}`
  return PAGES[id] ?? "/"
}

function useDebounced(value: string, ms: number) {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

/** Ctrl+K: opportunities, emails and pages, each with its type; Enter opens it. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { client } = useAuth()
  const navigate = useNavigate()
  const [q, setQ] = useState("")
  const term = useDebounced(q.trim(), 150)
  const search = useQuery({
    queryKey: keys.search(term),
    queryFn: () => unwrap(client.GET("/api/search", { params: { query: { q: term } } })),
    enabled: open && term.length >= 2,
  })
  const results = term.length >= 2 ? (search.data?.results ?? []) : []

  function go(type: string, id: string) {
    onOpenChange(false)
    setQ("")
    navigate(destination(type, id))
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search" description="Find opportunities, emails and pages"
                   className="sm:max-w-2xl">
      <Command shouldFilter={false}>
        <CommandInput value={q} onValueChange={setQ} placeholder="Search opportunities, emails and pages…" />
        <CommandList>
          <CommandEmpty>{term.length < 2 ? "Type at least two letters." : search.isFetching ? "Searching…" : "Nothing found."}</CommandEmpty>
          {GROUPS.map(({ type, heading, label }) => {
            const items = results.filter((r) => r.type === type)
            if (items.length === 0) return null
            return (
              <CommandGroup key={type} heading={heading}>
                {items.map((r) => (
                  <CommandItem key={`${type}-${r.id}`} value={`${type}-${r.id}`} onSelect={() => go(type, r.id)}>
                    <span className="min-w-0 truncate text-mist">{r.title}</span>
                    {r.subtitle && r.type !== "page" && <span className="min-w-0 truncate text-xs text-mist-2">{r.subtitle}</span>}
                    <span className="ml-auto shrink-0 text-xs text-mist-2">{label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )
          })}
        </CommandList>
        <p className="border-t border-line px-3 py-2 text-xs text-mist-2">↵ open · esc close · g o / g e / g r jump</p>
      </Command>
    </CommandDialog>
  )
}
