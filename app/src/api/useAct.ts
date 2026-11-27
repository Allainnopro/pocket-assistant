import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query"
import { useCallback } from "react"
import { toast } from "@/components/Toast"
import type { components } from "./schema"
import { useAuth } from "./auth"
import { unwrap } from "./client"
import type { Action } from "./events"
import { keys } from "./keys"
import { useOnline } from "./online"

type Row = components["schemas"]["OpportunityRow"]
type List = components["schemas"]["OpportunityList"]

const DONE: Record<Action, string> = { star: "Starred", unstar: "Unstarred", dismiss: "Dismissed", restore: "Restored", applied: "Marked as applied" }
const FAILED: Record<Action, string> = { star: "star it", unstar: "unstar it", dismiss: "dismiss it", restore: "restore it", applied: "mark it applied" }

function changed<T extends Row>(item: T, action: Action): T {
  if (action === "star" || action === "unstar") return { ...item, starred: action === "star" }
  return { ...item, status: action === "dismiss" ? "dismissed" : action === "applied" ? "applied" : "active" }
}

/** Does this row still belong in the tab after the change? (the engine's TABS in api_opportunities.py) */
function belongs(item: Row, tab: string): boolean {
  if (tab === "starred") return item.starred
  if (tab === "applied" || tab === "dismissed") return item.status === tab
  return item.status === "active"
}

function patched(list: List, key: QueryKey, id: number, action: Action): List {
  const tab = (key[1] as { tab?: string } | undefined)?.tab ?? "for_you"
  const item = list.items.find((r) => r.id === id)
  if (!item) return list
  const next = changed(item, action)
  if (belongs(next, tab)) return { ...list, items: list.items.map((r) => (r.id === id ? next : r)) }
  return { ...list, items: list.items.filter((r) => r.id !== id), total: list.total - 1, tab_total: list.tab_total - 1 }
}

/** Star, dismiss, applied…: the screen changes at once; a failed call puts it back and says so. */
export function useAct(): (id: number, action: Action) => void {  // does nothing while the engine is unreachable
  const { client } = useAuth()
  const online = useOnline()
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: ({ id, action }: { id: number; action: Action }) =>
      unwrap(client.POST("/api/opportunities/{opportunity_id}/{action}", { params: { path: { opportunity_id: id, action } } })),
    onMutate: async ({ id, action }) => {
      await queryClient.cancelQueries({ queryKey: ["opportunities"] })
      const lists = queryClient.getQueriesData<List>({ queryKey: ["opportunities"] })
      const detail = queryClient.getQueryData<Row>(keys.opportunity(id))
      for (const [key, list] of lists) if (list) queryClient.setQueryData(key, patched(list, key, id, action))
      if (detail) queryClient.setQueryData(keys.opportunity(id), changed(detail, action))
      return { lists, detail }
    },
    onError: (_error, { id, action }, saved) => {
      for (const [key, list] of saved?.lists ?? []) queryClient.setQueryData(key, list)
      if (saved?.detail) queryClient.setQueryData(keys.opportunity(id), saved.detail)
      toast(`Couldn't ${FAILED[action]}. Try again.`, "alert")
    },
    onSuccess: (_row, { action }) => toast(DONE[action]),
    onSettled: (_row, _error, { id }) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
      queryClient.invalidateQueries({ queryKey: ["tabCount"] }),
      queryClient.invalidateQueries({ queryKey: ["today"] }),
      queryClient.invalidateQueries({ queryKey: keys.opportunity(id) }),
    ]),
  })
  const { mutate } = mutation
  return useCallback((id: number, action: Action) => {
    if (online) mutate({ id, action })
  }, [mutate, online])
}
