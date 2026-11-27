import { useState, type FormEvent } from "react"
import { useAuth } from "@/api/auth"
import { makeClient } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { invoke } from "@/lib/tauri"

/** What an invoke rejection carries: the app's own message, as a string or an Error. */
const reason = (error: unknown) => (error instanceof Error ? error.message : String(error))

/** Desktop app only: connect this computer to the cloud engine. The address, then the laptop's old key if there is
 * one, otherwise a pairing code. Nothing is saved until the engine has answered. */
export function Connect({ notice }: { notice?: string }) {
  const { signIn, fetch } = useAuth()
  const [address, setAddress] = useState("")
  const [pairing, setPairing] = useState(false)
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function connect() {
    const target = address.trim()
    const baseUrl = await invoke<string>("check_address", { address: target })
    const host = new URL(baseUrl).host
    const unreachable = `Can't reach ${host}. Is Tailscale on, on this computer?`
    try {
      await (fetch ?? globalThis.fetch)(`${baseUrl}/api/health`)
    } catch {
      return setError(unreachable)
    }
    if (!pairing) {
      const legacy = await invoke<string | null>("legacy_key")
      if (legacy && (await signIn(legacy, baseUrl)) === "ok") return void (await invoke("save_connection", { address: target, key: legacy }))
      return setPairing(true)
    }
    const client = makeClient({ baseUrl, fetch, getPassword: () => null, onUnauthorized: () => {} })
    const { data, error: refused } = await client.POST("/api/pair", { body: { code: code.trim(), device_name: name.trim() } })
    if (!data) {
      const detail = (refused as { detail?: unknown } | undefined)?.detail
      return setError(typeof detail === "string" ? detail : "The engine refused that code.")
    }
    await invoke("save_connection", { address: target, key: data.key })
    if ((await signIn(data.key, baseUrl)) !== "ok") setError(unreachable)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await connect()
    } catch (failure) {
      setError(reason(failure))
    }
    setBusy(false)
  }

  return (
    <main className="grid min-h-svh place-items-center bg-ink p-6">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-line bg-panel p-6">
        <h1 className="font-serif text-2xl font-normal text-mist">Pocket Assistant</h1>
        {notice && <p className="text-sm text-mist">{notice}</p>}
        <p className="text-sm text-mist-2">Connect this computer to your engine.</p>
        <Field id="engine-address" label="Engine address">
          <Input id="engine-address" autoFocus autoComplete="off" spellCheck={false} placeholder="my-vm.tail1234.ts.net"
                 value={address} onChange={(e) => { setAddress(e.target.value); setPairing(false) }} />
        </Field>
        {pairing && (
          <>
            <p className="text-sm text-mist-2">
              Make a code on a computer that is already connected: <code className="text-mist">assistant pair</code>
            </p>
            <Field id="pairing-code" label="Pairing code">
              <Input id="pairing-code" autoComplete="off" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Field id="device-name" label="Name for this computer">
              <Input id="device-name" autoComplete="off" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          </>
        )}
        {error && <p role="alert" className="text-sm text-mist">{error}</p>}
        <Button type="submit" disabled={busy || address.trim() === "" || (pairing && (code.trim() === "" || name.trim() === ""))}>Connect</Button>
      </form>
    </main>
  )
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 text-sm text-mist-2">
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  )
}
