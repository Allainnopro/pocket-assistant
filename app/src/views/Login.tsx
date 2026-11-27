import { Eye, EyeOff } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import { useAuth } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { invoke, isTauri, type Connection } from "@/lib/tauri"
import { Connect } from "./Connect"

const ERRORS = {
  wrong: "That password didn't work. Copy it again from the engine's window.",
  unreachable: "Can't reach the engine at 127.0.0.1:47615. Start it with assistant engine --dev.",
}

/** The dev server (vite.config.ts) passes on the password `assistant engine --dev` left for this page. */
export const DEV_HANDOFF = "/__dev/launch-password"

/** In development, sign in with the handed-over password; true while trying, false once the form is needed. */
function useDevSignIn(): boolean {
  const { signIn, fetch: injected } = useAuth()
  const [trying, setTrying] = useState(import.meta.env.DEV)
  useEffect(() => {
    if (!import.meta.env.DEV) return
    let cancelled = false
    void (async () => {
      try {
        const response = await (injected ?? globalThis.fetch)(new URL(DEV_HANDOFF, window.location.origin))
        const password = response.ok ? (await response.text()).trim() : ""
        if (password && !cancelled && (await signIn(password)) === "ok") return
      } catch {
        // no dev server or no engine: the form takes over
      }
      if (!cancelled) setTrying(false)
    })()
    return () => {
      cancelled = true
    }
  }, [signIn, injected])
  return trying
}

const RETRYING = "Can't reach the engine. Retrying…"

/** Inside the desktop app: sign in with the device key the app remembers, retrying each second while the engine
 * can't be reached. No saved connection, or a key the engine no longer knows (a 401, also after being signed out
 * mid-session), goes to Connect. */
function AppSignIn() {
  const { signIn } = useAuth()
  const [status, setStatus] = useState("Connecting…")
  const [connect, setConnect] = useState<{ notice?: string } | null>(null)
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function attempt() {
      try {
        const saved = await invoke<Connection | null>("load_connection")
        if (saved === null) return void (!cancelled && setConnect({}))
        const result = await signIn(saved.key, saved.baseUrl)
        if (result === "ok") return
        if (result === "wrong") {
          await invoke("forget_connection")
          return void (!cancelled && setConnect({ notice: "This computer was disconnected. Pair it again." }))
        }
        if (!cancelled) setStatus(RETRYING)
      } catch {
        if (!cancelled) setStatus(RETRYING)
      }
      if (!cancelled) timer = setTimeout(attempt, 1000)
    }
    void attempt()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [signIn])
  if (connect) return <Connect notice={connect.notice} />
  return <main className="grid min-h-svh place-items-center bg-ink p-6 text-center text-sm text-mist-2">{status}</main>
}

export function Login() {
  return isTauri() ? <AppSignIn /> : <BrowserLogin />
}

function BrowserLogin() {
  const { signIn } = useAuth()
  const trying = useDevSignIn()
  const [candidate, setCandidate] = useState("")
  const [error, setError] = useState<keyof typeof ERRORS | null>(null)
  const [busy, setBusy] = useState(false)
  const [visible, setVisible] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    const result = await signIn(candidate.trim())
    setBusy(false)
    if (result !== "ok") setError(result)
  }

  if (trying) return <main className="grid min-h-svh place-items-center bg-ink text-sm text-mist-2">Signing in…</main>
  return (
    <main className="grid min-h-svh place-items-center bg-ink p-6">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-line bg-panel p-6">
        <h1 className="font-serif text-2xl font-normal text-mist">Pocket Assistant</h1>
        <p className="text-sm text-mist-2">
          Paste the launch password printed by <code className="text-mist">assistant engine --dev</code>.
        </p>
        <div className="flex flex-col gap-1.5 text-sm text-mist-2">
          <label htmlFor="launch-password">Launch password</label>
          <div className="relative">
            <Input id="launch-password" type={visible ? "text" : "password"} autoFocus autoComplete="off" spellCheck={false}
                   value={candidate} onChange={(e) => setCandidate(e.target.value)} className="pr-9" />
            <button type="button" onClick={() => setVisible((v) => !v)} aria-pressed={visible}
                    aria-label={visible ? "Hide password" : "Show password"}
                    className="lamp-focus absolute inset-y-0 right-0 grid w-9 place-items-center rounded-r-lg text-mist-2 hover:text-mist">
              {visible ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
            </button>
          </div>
        </div>
        {error && <p role="alert" className="text-sm text-mist">{ERRORS[error]}</p>}
        <Button type="submit" disabled={busy || candidate.trim() === ""}>Connect</Button>
      </form>
    </main>
  )
}
