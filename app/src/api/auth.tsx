import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react"
import { makeClient, type ApiClient } from "./client"

/** sessionStorage only: gone when the tab closes, and a new password comes with every engine start anyway. */
export const PASSWORD_KEY = "pocket.launchPassword"

type SignIn = "ok" | "wrong" | "unreachable"
type AuthValue = {
  password: string | null
  client: ApiClient
  signIn: (candidate: string) => Promise<SignIn>
  signOut: () => void
  fetch?: typeof globalThis.fetch // injected in tests; the event stream uses it too
}

const AuthContext = createContext<AuthValue | null>(null)

function stored(): string | null {
  try {
    return sessionStorage.getItem(PASSWORD_KEY)
  } catch {
    return null // storage blocked: ask each time
  }
}

function store(password: string | null) {
  try {
    if (password === null) sessionStorage.removeItem(PASSWORD_KEY)
    else sessionStorage.setItem(PASSWORD_KEY, password)
  } catch {
    // storage blocked: the password still lives in memory for this page
  }
}

export function AuthProvider({ children, fetch }: { children: ReactNode; fetch?: typeof globalThis.fetch }) {
  const [password, setPassword] = useState<string | null>(stored)
  const current = useRef(password)
  current.current = password

  const signOut = useCallback(() => {
    store(null)
    setPassword(null)
  }, [])

  const client = useMemo(
    () => makeClient({ fetch, getPassword: () => current.current, onUnauthorized: signOut }),
    [fetch, signOut],
  )

  const signIn = useCallback(
    async (candidate: string): Promise<SignIn> => {
      const probe = makeClient({ fetch, getPassword: () => candidate, onUnauthorized: () => {} })
      try {
        const { response } = await probe.GET("/api/settings")
        if (response.ok) {
          store(candidate)
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

  const value = useMemo(() => ({ password, client, signIn, signOut, fetch }), [password, client, signIn, signOut, fetch])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error("useAuth needs an AuthProvider above it")
  return value
}
