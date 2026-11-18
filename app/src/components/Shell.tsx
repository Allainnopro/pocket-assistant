import { useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { useShortcuts } from "@/lib/keys"
import { cn } from "@/lib/utils"
import { CommandPalette } from "./CommandPalette"
import { Toaster } from "./Toast"

/** The emails list's default query (Emails view, Important, 7 days): shared, so the count costs nothing. */
export const IMPORTANT_EMAILS = { section: "important", days: 7 } as const

function useCounts() {
  const { client } = useAuth()
  const forYou = useQuery({
    queryKey: keys.tabCount("for_you"),
    queryFn: () => unwrap(client.GET("/api/opportunities", { params: { query: { tab: "for_you", limit: 1 } } })),
  })
  const emails = useQuery({
    queryKey: keys.emails(IMPORTANT_EMAILS),
    queryFn: () => unwrap(client.GET("/api/emails", { params: { query: IMPORTANT_EMAILS } })),
  })
  return { opportunities: forYou.data?.tab_total, emails: emails.data?.items.length }
}

const NAV = [
  { to: "/", label: "Today", count: undefined },
  { to: "/opportunities", label: "Opportunities", count: "opportunities" },
  { to: "/emails", label: "Emails", count: "emails" },
  { to: "/reports", label: "Reports", count: undefined },
  { to: "/settings", label: "Settings", count: undefined },
] as const

export function Shell() {
  const navigate = useNavigate()
  const counts = useCounts()
  const [palette, setPalette] = useState(false)
  const { pathname } = useLocation()
  const nav = useRef<HTMLElement>(null)
  const [marker, setMarker] = useState<{ top: number; height: number } | null>(null)
  useLayoutEffect(() => { // the amber marker slides to the current page's tab
    const current = nav.current?.querySelector<HTMLElement>('[aria-current="page"]')
    setMarker(current ? { top: current.offsetTop, height: current.offsetHeight } : null)
  }, [pathname])

  useShortcuts({
    "g t": () => navigate("/"),
    "g o": () => navigate("/opportunities"),
    "g e": () => navigate("/emails"),
    "g r": () => navigate("/reports"),
    "g s": () => navigate("/settings"),
    "/": () => setPalette(true),
  })
  useEffect(() => {
    function onKey(event: KeyboardEvent) { // Ctrl+K (or Cmd+K) toggles the palette, even from a field
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setPalette((open) => !open)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  return (
    <div className="flex h-svh bg-ink text-mist">
      <aside className="flex w-52 shrink-0 flex-col border-r border-line px-2.5 py-4">
        <div className="mx-2 mb-4 flex items-center gap-2 font-semibold">
          <span className="size-2.5 rounded-[3px] bg-lamp" aria-hidden />
          Pocket
        </div>
        <nav ref={nav} className="relative flex flex-col gap-2">
          {marker && (
            <span data-marker aria-hidden style={{ transform: `translateY(${marker.top}px)`, height: marker.height }}
                  className="pointer-events-none absolute top-0 left-0 w-0.5 rounded-full bg-lamp transition-transform duration-300 ease-[cubic-bezier(0.3,1.35,0.5,1)]" />
          )}
          {NAV.map(({ to, label, count }) => (
            <NavLink key={to} to={to} end={to === "/"}
                     className={({ isActive }) => cn("flex items-center justify-between rounded-md px-2.5 py-1.5 text-sm transition-colors lamp-focus",
                       isActive ? "bg-hover text-mist" : "text-mist-2 hover:text-mist")}>
              {({ isActive }) => (
                <>
                  <span className={isActive ? undefined : "lamp-underline"}>{label}</span>
                  {count && counts[count] !== undefined && <span className="text-xs text-mist-2 tabular-nums">{counts[count]}</span>}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <button type="button" onClick={() => setPalette(true)}
                className="lamp-hover lamp-focus mt-auto flex items-center justify-between rounded-md border border-line px-2.5 py-1.5 text-xs text-mist-2">
          <span className="flex items-center gap-1.5"><Search className="size-3.5" aria-hidden />Search</span>
          <kbd className="rounded border border-line px-1 text-[10.5px]">Ctrl K</kbd>
        </button>
      </aside>
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
      <CommandPalette open={palette} onOpenChange={setPalette} />
      <Toaster />
    </div>
  )
}
