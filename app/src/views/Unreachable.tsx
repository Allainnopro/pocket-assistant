import { isTauri } from "@/lib/tauri"

export function Unreachable() {
  const app = isTauri()
  return (
    <main className="grid min-h-svh place-items-center bg-ink p-6">
      <div className="flex max-w-sm flex-col gap-2 text-center">
        <h1 className="font-serif text-2xl font-normal text-mist">
          {app ? "Can't reach the engine." : "Can't reach the engine at 127.0.0.1:47615."}
        </h1>
        {app ? (
          <p className="text-sm text-mist-2">Restart it from the tray icon.</p>
        ) : (
          <p className="text-sm text-mist-2">
            Start it with <code className="text-mist">assistant engine --dev</code>
          </p>
        )}
        <p className="text-sm text-mist-2">Retrying…</p>
      </div>
    </main>
  )
}
