import { useAuth } from "@/api/auth"
import { isTauri } from "@/lib/tauri"

export function Unreachable() {
  const { baseUrl } = useAuth()
  if (isTauri()) {
    return (
      <main className="grid min-h-svh place-items-center bg-ink p-6 text-center text-sm text-mist-2">
        Can't reach the engine at {baseUrl.replace(/^https?:\/\//, "")}. Retrying…
      </main>
    )
  }
  return (
    <main className="grid min-h-svh place-items-center bg-ink p-6">
      <div className="flex max-w-sm flex-col gap-2 text-center">
        <h1 className="font-serif text-2xl font-normal text-mist">Can't reach the engine at 127.0.0.1:47615.</h1>
        <p className="text-sm text-mist-2">
          Start it with <code className="text-mist">assistant engine --dev</code>
        </p>
        <p className="text-sm text-mist-2">Retrying…</p>
      </div>
    </main>
  )
}
