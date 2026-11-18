import { render, screen } from "@testing-library/react"
import { describe, expect, test } from "vitest"
import { Deadline } from "./Deadline"
import { EmptyState } from "./EmptyState"
import { KindDot } from "./KindDot"
import { KindPill } from "./KindPill"
import { ScoreBar } from "./ScoreBar"
import { StatusLine } from "./StatusLine"
import { TopPick } from "./TopPick"

const NOW = new Date("2026-11-17T21:00:00Z") // 04:00 on the 18th in HCMC

describe("Deadline", () => {
  test("urgent is lamp with a clock; later is mist-2", () => {
    const { container, rerender } = render(<Deadline deadline="2026-11-18" now={NOW} />)
    const urgent = screen.getByText("in 20h")
    expect(urgent).toHaveClass("text-lamp")
    expect(container.querySelector("svg")).not.toBeNull()
    rerender(<Deadline deadline="2026-12-20" now={NOW} />)
    expect(screen.getByText(/in \d+ days/)).toHaveClass("text-mist-2")
    expect(container.querySelector("svg")).toBeNull()
  })

  test("overdue says so in bold mist, not amber", () => {
    render(<Deadline deadline="2026-11-01" now={NOW} />)
    const overdue = screen.getByText("overdue")
    expect(overdue).toHaveClass("text-mist", "font-semibold")
    expect(overdue).not.toHaveClass("text-lamp")
  })
})

test("ScoreBar shows the number and its segments", () => {
  const { container } = render(<ScoreBar score={71} />)
  expect(screen.getByText("71")).toBeInTheDocument()
  const bars = container.querySelectorAll("[data-segment]")
  expect([...bars].map((b) => b.getAttribute("data-segment"))).toEqual(["on", "on", "off"])
})

test("KindPill shows the word, never a colour", () => {
  render(<KindPill kind="job_fair" />)
  const pill = screen.getByText("Job fair")
  expect(pill.closest("[data-kind]")).toHaveClass("border-line", "text-mist-2")
})

test("only an on TopPick is lit", () => {
  const { container, rerender } = render(<TopPick on />)
  expect(container.firstElementChild).toHaveClass("bg-lamp")
  expect(screen.getByLabelText("Top pick")).toBeInTheDocument()
  rerender(<TopPick on={false} />)
  expect(container.firstElementChild).not.toHaveClass("bg-lamp")
})

describe("StatusLine", () => {
  test("online shows the last check and the spend", () => {
    render(<StatusLine lastCheck="2026-11-17T14:42:00+00:00" problems={{}} spend="$0.08 of $5.00 this month" />)
    expect(screen.getByText("Checked 21:42 · Online · $0.08 of $5.00 this month")).toBeInTheDocument()
  })

  test("a hard failure shows in alert red with its fix, ahead of quiet problems", () => {
    render(<StatusLine lastCheck={null} spend="" problems={{
      anthropic: { problem: "busy" },
      gmail: { problem: "auth_lost" },
    }} />)
    const alert = screen.getByText("Gmail sign-in expired")
    expect(alert.closest("[data-severity]")).toHaveClass("text-alert")
    expect(screen.getByText("assistant set-key gmail")).toBeInTheDocument()
  })
})

test("EmptyState shows its one line", () => {
  render(<EmptyState line="Nothing needs you today." />)
  expect(screen.getByText("Nothing needs you today.")).toBeInTheDocument()
})

test("every button gets the amber hover edge and focus ring", async () => {
  const { Button } = await import("./ui/button")
  render(<><Button>Applied</Button><Button variant="outline">Dismiss</Button></>)
  for (const name of ["Applied", "Dismiss"]) expect(screen.getByRole("button", { name })).toHaveClass("lamp-hover", "lamp-focus")
})

describe("KindDot", () => {
  test("a dot names its kind, and urgent mail says so", () => {
    render(<><KindDot kind="scholarship" urgent={false} /><KindDot kind="uni_admin" urgent /></>)
    expect(screen.getByRole("img", { name: "Scholarships" })).toHaveAttribute("data-urgent", "false")
    expect(screen.getByRole("img", { name: "Urgent · Uni admin" })).toHaveAttribute("data-urgent", "true")
  })

  test("unsorted mail gets a hollow ring", () => {
    render(<KindDot kind="unsorted" urgent={false} />)
    expect(screen.getByRole("img", { name: "Unsorted" })).toHaveAttribute("data-hollow", "true")
  })
})

test("a kind the dashboard doesn't know shows as unsorted instead of breaking the page", () => {
  // an older or newer engine than this dashboard could send one
  render(<KindDot kind={"brand_new" as never} urgent={false} />)
  expect(screen.getByRole("img", { name: "Unsorted" })).toBeInTheDocument()
})
