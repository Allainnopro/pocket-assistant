import { createContext, useContext } from "react"

const OnlineContext = createContext(true)

/** False while the engine can't be reached: actions wait for it instead of failing. */
export const OnlineProvider = OnlineContext.Provider
export const useOnline = (): boolean => useContext(OnlineContext)
