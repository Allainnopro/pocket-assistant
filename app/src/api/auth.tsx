import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { isTauri, listen } from "@/lib/tauri"
import { API_BASE, makeClient, type ApiClient } from "./client"

/** Browser mode only: sessionStorage is gone when the tab closes, and a new password comes with every engine start.
 * In the desktop app the device key comes from the app each launch and never touches web storage. */
export const PASSWORD_KEY = "pocket.launchPassword"

type SignIn = "ok" | "wrong" | "unreachable"
type AuthValue = {
  password: string | null
  baseUrl: string
  client: ApiClient
  signIn: (candidate: string, baseUrl?: string) => Promise<SignIn>
  signOut: () => void
  fetch?: typeof globalThis.fetch // injected in tests; the event stream uses it too
}

const AuthContext = createContext<AuthValue | null>(null)

function stored(): string | null {
  if (isTauri()) return null
  try {
    return sessionStorage.getItem(PASSWORD_KEY)
  } catch {
    return null // storage blocked: ask each time
  }
}

function store(password: string | null) {
  if (isTauri()) return
  try {
    if (password === null) sessionStorage.removeItem(PASSWORD_KEY)
    else sessionStorage.setItem(PASSWORD_KEY, password)
  } catch {
    // storage blocked: the password still lives in memory for this page
  }
}

export function AuthProvider({ children, fetch }: { children: ReactNode; fetch?: typeof globalThis.fetch }) {
  const [password, setPassword] = useState<string | null>(stored)
  const [baseUrl, setBaseUrl] = useState(API_BASE)
  const current = useRef(password)
  current.current = password

  // the app forgot this computer (tray menu): start over at the Connect screen
  useEffect(() => (isTauri() ? listen("connection-forgotten", () => window.location.reload()) : undefined), [])

  const signOut = useCallback(() => {
    store(null)
    setPassword(null)
  }, [])

  const client = useMemo(
    () => makeClient({ baseUrl, fetch, getPassword: () => current.current, onUnauthorized: signOut }),
    [baseUrl, fetch, signOut],
  )

  const signIn = useCallback(
    async (candidate: string, target: string = API_BASE): Promise<SignIn> => {
      const probe = makeClient({ baseUrl: target, fetch, getPassword: () => candidate, onUnauthorized: () => {} })
      try {
        const { response } = await probe.GET("/api/settings")
        if (response.ok) {
          store(candidate)
          setBaseUrl(target)
          setPassword(candidate)
          return "ok"
        }
        return response.status === 401 || response.status === 403 ? "wrong" : "unreachable"
      } catch {
        return "unreachable"
      }
    },
    [fetch],
  )

  const value = useMemo(() => ({ password, baseUrl, client, signIn, signOut, fetch }), [password, baseUrl, client, signIn, signOut, fetch])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error("useAuth needs an AuthProvider above it")
  return value
}
