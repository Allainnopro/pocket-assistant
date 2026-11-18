import { act, render, screen } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import css from "../index.css?raw"
import { rowStep, useCountUp } from "./motion"

const block = (start: RegExp) => {
  const at = css.search(start)
  if (at < 0) return ""
  let depth = 0
  for (let i = css.indexOf("{", at); i < css.length; i++) {
    if (css[i] === "{") depth++
    if (css[i] === "}" && --depth === 0) return css.slice(at, i + 1)
  }
  return ""
}

function reducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches, media: query, addEventListener() {}, removeEventListener() {} }))
}

function Count({ value }: { value: number }) {
  return <p>{useCountUp(value)}</p>
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test("rowStep caps after 8 rows, so a long list never crawls in", () => {
  expect(rowStep(3, 0)["--i" as never]).toBe(3)
  expect(rowStep(3, 7)["--i" as never]).toBe(10)
  expect(rowStep(3, 8)["--i" as never]).toBe(11)
  expect(rowStep(3, 200)["--i" as never]).toBe(11)
})

test("the arrival keeps no transform after it ends, so the hover growth still works", () => {
  const arrive = block(/@utility arrive\b/)
  expect(arrive).toMatch(/backwards/)
  expect(arrive).not.toMatch(/forwards|\sboth\b/)
})

test("reduced motion switches the motion off", () => {
  const reduce = block(/@media \(prefers-reduced-motion: reduce\)/)
  for (const name of [".arrive", ".panel-in", ".lift"]) expect(reduce).toContain(name)
  expect(reduce).toMatch(/animation:\s*none/)
  expect(reduce).toMatch(/scale:\s*none/)
  expect(reduce).toMatch(/margin-block:\s*0/)
})

test("the count-up ends on the value", () => {
  reducedMotion(false)
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "Date", "setTimeout", "clearTimeout"] })
  render(<Count value={19} />)
  act(() => vi.advanceTimersByTime(200))
  const partway = Number(screen.getByRole("paragraph").textContent)
  expect(partway).toBeGreaterThan(0)
  expect(partway).toBeLessThan(19)
  act(() => vi.advanceTimersByTime(700))
  expect(screen.getByRole("paragraph")).toHaveTextContent("19")
})

test("under reduced motion the count-up shows the value at once", () => {
  reducedMotion(true)
  render(<Count value={19} />)
  expect(screen.getByRole("paragraph")).toHaveTextContent("19")
})

test("a lifted row keeps its hover area while it makes room, so it never shakes under a still pointer", () => {
  // the hovered row moves down by its new margin; without this, a pointer near its top edge falls into the gap
  expect(block(/@utility lift\b/)).toMatch(/&:hover::before\s*\{[^}]*content:\s*"";[^}]*inset:\s*-6px 0/)
  expect(block(/@media \(prefers-reduced-motion: reduce\)/)).toMatch(/\.lift:hover::before\s*\{[^}]*content:\s*none/)
})

const pages = import.meta.glob<string>("../views/*.tsx", { query: "?raw", import: "default", eager: true })

test("the wind-up stays inside the narrowest page padding (24 px), so pages need no sideways clip", () => {
  const pulls = [...block(/@keyframes arrive\b/).matchAll(/translate:\s*(-?\d+)px/g)].map((m) => Number(m[1]))
  expect(pulls.length).toBeGreaterThan(0)
  expect(Math.max(...pulls)).toBeLessThanOrEqual(20)
  for (const [path, text] of Object.entries(pages)) expect([path, text.includes("overflow-x-hidden")]).toEqual([path, false])
})

test("rows that grow on hover rise into place instead of sliding sideways, so nothing ever overflows the page", () => {
  // a row sliding in under the pointer would also be 2 % wider: together more than the 24 px padding
  expect(css).toMatch(/\.arrive\.lift\s*\{[^}]*animation-name:\s*rise/)
  const rise = block(/@keyframes rise\b/)
  const sideways = [...rise.matchAll(/translate:\s*(-?\d+)(?:px)?\s+-?\d+(?:px)?/g)].map((m) => Number(m[1]))
  expect(sideways.length).toBeGreaterThan(0)
  expect(sideways.every((x) => x === 0)).toBe(true)
  expect(block(/@media \(prefers-reduced-motion: reduce\)/)).toContain(".arrive.lift")
  for (const [path, text] of Object.entries(pages)) expect([path, /overflow-x-(clip|hidden)/.test(text)]).toEqual([path, false])
})
