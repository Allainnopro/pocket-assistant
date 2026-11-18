/// <reference types="vitest/config" />
import fs from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"

/** Dev server only: passes the launch password that `assistant engine --dev` left in app/.launch-password to
 * the dashboard's own page, so nobody copies it out of a terminal. Other sites' requests are refused:
 * browsers mark them cross-site, and they couldn't read the answer anyway (no CORS headers). */
function devPassword(): Plugin {
  const handoff = path.resolve(import.meta.dirname, ".launch-password")
  return {
    name: "pocket-dev-password",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__dev/launch-password", (req, res) => {
        if (req.method !== "GET" || req.headers["sec-fetch-site"] !== "same-origin") {
          res.statusCode = 403
          res.end()
          return
        }
        let password = ""
        try {
          password = fs.readFileSync(handoff, "utf8").trim()
        } catch {
          // the engine isn't running in dev mode
        }
        res.statusCode = password ? 200 : 404
        res.setHeader("Content-Type", "text/plain; charset=utf-8")
        res.setHeader("Cache-Control", "no-store")
        res.end(password)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), devPassword()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  server: { port: 5173, strictPort: true },
  // css.include: let the token test read index.css (Vitest skips CSS otherwise, even with ?raw)
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], css: { include: [/index\.css(\?|$)/] } },
})
