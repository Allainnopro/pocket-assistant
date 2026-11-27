import { createContext, useContext } from "react"

export const APP_RETRY_MS = 30000 // the app talks to a VM: be gentle with it

const OnlineContext = createContext(true)

/** False while the engine can't be reached: actions wait for it instead of failing. */
export const OnlineProvider = OnlineContext.Provider
export const useOnline = (): boolean => useContext(OnlineContext)
