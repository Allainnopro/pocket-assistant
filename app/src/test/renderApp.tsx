import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { createMemoryRouter } from "react-router"
import { RouterProvider } from "react-router/dom"
import { AuthProvider, PASSWORD_KEY } from "@/api/auth"
import { TooltipProvider } from "@/components/ui/tooltip"
import { routes } from "@/router"
import { fakeFetch } from "./fakeFetch"

type Handler = (request: Request) => Response | Promise<Response>

/** JSON for a path, or a 404: the fake engine behind a test. */
export function engine(answers: Record<string, unknown | ((url: URL, request: Request) => unknown)>): Handler {
  return async (request) => {
    const url = new URL(request.url)
    const answer = answers[url.pathname]
    if (answer === undefined) return Response.json({ detail: "Not Found" }, { status: 404 })
    const body = typeof answer === "function" ? await (answer as (u: URL, r: Request) => unknown)(url, request) : answer
    return body instanceof Response ? body : Response.json(body)
  }
}

/** The dashboard at `path`, signed in, on a fake engine. */
export function renderApp(path: string, handler: Handler) {
  sessionStorage.setItem(PASSWORD_KEY, "pw")
  const { fetch, calls } = fakeFetch(handler)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AuthProvider fetch={fetch}>
          <RouterProvider router={router} />
        </AuthProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...view, router, calls, queryClient }
}

export const location = (router: { state: { location: { pathname: string; search: string } } }) =>
  router.state.location.pathname + router.state.location.search
