import type { RouteObject } from "react-router"
import { createHashRouter } from "react-router"
import { Shell } from "./components/Shell"
import { Emails } from "./views/Emails"
import { Opportunities } from "./views/Opportunities"
import { Reports } from "./views/Reports"
import { Settings } from "./views/Settings"
import { Today } from "./views/Today"

// Search params hold the filters and the open panel, so a view can be linked to and survives a reload.
export const routes: RouteObject[] = [
  {
    path: "/",
    Component: Shell,
    children: [
      { index: true, Component: Today },
      { path: "opportunities", Component: Opportunities },
      { path: "emails", Component: Emails },
      { path: "reports", Component: Reports },
      { path: "settings", Component: Settings },
    ],
  },
]

/** A hash router: M4 serves these files from Tauri, with no server to send every path to index.html. */
export const router = createHashRouter(routes)
