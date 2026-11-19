import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, TriangleAlert } from "lucide-react"
import type { CSSProperties, ReactNode } from "react"
import { useAuth } from "@/api/auth"
import { unwrap } from "@/api/client"
import { keys } from "@/api/keys"
import { LoadError } from "@/components/EmptyState"
import { toast } from "@/components/Toast"
import { Button } from "@/components/ui/button"
import { KINDS, kindOf } from "@/lib/kinds"
import { step } from "@/lib/motion"
import { invoke, isTauri } from "@/lib/tauri"

// A plain list: big bold headings split the sections; no boxes.
const section = "flex flex-col"
const heading = "arrive mb-2 text-lg font-semibold text-mist"
const money = (usd: number) => `$${usd.toFixed(2)}`

/** A command or path the browser can't run or open: shown, with a Copy button. */
function Copyable({ text }: { text: string }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      toast("Copied")
    } catch {
      toast("Couldn't copy. Select the text instead.", "alert")
    }
  }
  return (
    <span data-copy className="flex min-w-0 items-center gap-2">
      <code className="min-w-0 truncate rounded bg-hover px-1.5 py-0.5 text-xs text-mist" title={text}>{text}</code>
      <Button size="xs" variant="outline" onClick={copy}>Copy</Button>
    </span>
  )
}

function Connection({ name, detail, children, style }: { name: string; detail?: string; children: ReactNode; style: CSSProperties }) {
  return (
    <div data-connection style={style} className="arrive flex items-center justify-between gap-4 border-t border-line py-2.5">
      <span className="flex min-w-0 flex-col">
        <span className="text-sm text-mist">{name}</span>
        {detail && <span className="truncate text-xs text-mist-2">{detail}</span>}
      </span>
      <span className="flex shrink-0 items-center gap-3 text-sm">{children}</span>
    </div>
  )
}

const ok = (words: string) => <span className="flex items-center gap-1.5 text-mist-2"><Check className="size-4" aria-hidden />{words}</span>
const needsFix = (words: string, command: string) => (
  <>
    <span data-severity="alert" className="flex items-center gap-1.5 text-alert"><TriangleAlert className="size-4" aria-hidden />{words}</span>
    <Copyable text={command} />
  </>
)

function Limit({ label, value, setting, style }: { label: string; value: string; setting: string; style: CSSProperties }) {
  return (
    <div style={style} className="arrive flex items-center justify-between border-t border-line py-2 text-sm">
      <span className="text-mist">{label}</span>
      <span className="flex items-center gap-4">
        <code className="text-xs text-mist-2">{setting}</code>
        <span className="w-14 text-right text-mist tabular-nums">{value}</span>
      </span>
    </div>
  )
}

export function Settings() {
  const { client } = useAuth()
  const queryClient = useQueryClient()
  const { data, isError, refetch } = useQuery({ queryKey: keys.settings, queryFn: () => unwrap(client.GET("/api/settings")) })
  const rescore = useMutation({
    mutationFn: () => unwrap(client.POST("/api/settings/rescore")),
    onSuccess: (result) => {
      toast(`Rescored ${result.rescored} · ${result.expired} expired`)
      void queryClient.invalidateQueries({ queryKey: ["opportunities"] })
      void queryClient.invalidateQueries({ queryKey: ["tabCount"] })
      void queryClient.invalidateQueries({ queryKey: ["today"] })
    },
    onError: () => toast("Couldn't rescore. Try again.", "alert"),
  })
  if (!data) return isError ? <LoadError retry={refetch} /> : null
  const { gmail, anthropic, telegram } = data.connections
  const { profile } = data

  return (
    <div className="h-full overflow-y-auto px-8 py-7">
      <div className="flex max-w-3xl flex-col gap-8">
        <h1 className="arrive font-serif text-2xl font-normal text-mist" style={step(0)}>Settings</h1>

        <section className={section}>
          <h2 className={heading} style={step(1)}>Connected accounts</h2>
          <Connection name="Gmail" detail={gmail.address} style={step(2)}>
            {gmail.signed_in ? ok("Signed in") : needsFix("Sign-in expired", "assistant set-key gmail")}
          </Connection>
          <Connection name="Anthropic" style={step(3)}>
            {anthropic.key_saved ? ok("Key saved") : needsFix("No key yet", "assistant set-key anthropic")}
          </Connection>
          <Connection name="Telegram" style={step(4)}>
            {telegram.paired ? ok("Paired") : <span className="text-mist-2">Arrives with Telegram</span>}
          </Connection>
        </section>

        <section className={section}>
          <h2 className={heading} style={step(3)}>Limits</h2>
          <Limit label="Monthly soft cap" value={money(data.caps.monthly_soft_usd)} setting="[llm] monthly_soft_cap_usd" style={step(4)} />
          <Limit label="Daily cap" value={money(data.caps.daily_usd)} setting="[llm] daily_cap_usd" style={step(5)} />
          <Limit label="For you minimum score" value={String(data.for_you_min_score)} setting="[dashboard] for_you_min_score" style={step(6)} />
          <p className="arrive pt-1 text-xs text-mist-2" style={step(7)}>Restart the engine after changing config.toml.</p>
        </section>

        <section className={section}>
          <h2 className={heading} style={step(5)}>Profile</h2>
          <dl style={step(6)} className="arrive grid grid-cols-[9rem_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-mist-2">Level</dt><dd className="text-mist">{profile.level}</dd>
            <dt className="text-mist-2">Interests</dt><dd className="text-mist">{profile.interests.join(", ")}</dd>
            <dt className="text-mist-2">Open to</dt><dd className="text-mist">{profile.open_to.map((k) => KINDS[kindOf(k)].label).join(", ")}</dd>
            <dt className="text-mist-2">Home</dt><dd className="text-mist">{profile.home}</dd>
            <dt className="text-mist-2">Remote</dt><dd className="text-mist">{profile.remote_ok}</dd>
          </dl>
          <div style={step(7)} className="arrive mt-3 flex items-center justify-between gap-4 border-t border-line pt-3">
            <span className="flex min-w-0 items-center gap-2">
              <Copyable text={data.profile_path} />
              {isTauri() && (  // the desktop app can open it in the default editor; a browser can't
                <Button size="xs" variant="outline"
                        onClick={() => void invoke("open_profile", { path: data.profile_path })
                          .catch(() => toast("Couldn't open it. Copy the path instead.", "alert"))}>Open</Button>
              )}
            </span>
            <Button size="sm" variant="outline" disabled={rescore.isPending} onClick={() => rescore.mutate()}>Rescore</Button>
          </div>
          <p className="arrive pt-1 text-xs text-mist-2" style={step(8)}>Edit profile.toml to change what counts as a good fit; the engine picks it up within 30 seconds.</p>
        </section>
      </div>
    </div>
  )
}
