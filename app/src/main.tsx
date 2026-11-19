import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router/dom"
import { AuthProvider } from "./api/auth"
import { App } from "./App"
import { TooltipProvider } from "./components/ui/tooltip"
import "./index.css"
import { focusFollowsTheWindow, installExternalLinks, isTauri } from "./lib/tauri"
import { router } from "./router"

if (isTauri()) {
  installExternalLinks() // links for a new tab open in the default browser
  focusFollowsTheWindow() // opening the window from the tray refreshes stale data
}

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AuthProvider>
          <App>
            <RouterProvider router={router} />
          </App>
        </AuthProvider>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
