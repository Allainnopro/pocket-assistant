import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { useShortcuts } from "./keys"

function Keys({ map }: { map: Record<string, () => void> }) {
  useShortcuts(map)
  return <input aria-label="Filter" />
}

const press = (key: string, target: Element | Window = window, extra: KeyboardEventInit = {}) =>
  fireEvent.keyDown(target, { key, ...extra })

afterEach(() => vi.useRealTimers())

test("g o goes to Opportunities and g e to Emails", () => {
  const map = { "g o": vi.fn(), "g e": vi.fn(), s: vi.fn() }
  render(<Keys map={map} />)
  press("g"); press("o")
  press("g"); press("e")
  expect(map["g o"]).toHaveBeenCalledTimes(1)
  expect(map["g e"]).toHaveBeenCalledTimes(1)
  expect(map.s).not.toHaveBeenCalled()
})

test("a sequence times out after 1 second", () => {
  vi.useFakeTimers()
  const map = { "g o": vi.fn(), s: vi.fn() }
  render(<Keys map={map} />)
  press("g")
  vi.advanceTimersByTime(1100)
  press("o")
  expect(map["g o"]).not.toHaveBeenCalled()
  press("g")
  vi.advanceTimersByTime(1100)
  press("s") // too late for "g s": it stars
  expect(map.s).toHaveBeenCalledTimes(1)
})

test("shortcuts don't fire while typing", () => {
  // Review Focus 4: typing "go s" in the filter box must stay in the box
  const map = { "g o": vi.fn(), s: vi.fn() }
  render(<Keys map={map} />)
  const input = screen.getByLabelText("Filter")
  input.focus()
  press("g", input); press("o", input); press("s", input)
  expect(map["g o"]).not.toHaveBeenCalled()
  expect(map.s).not.toHaveBeenCalled()
})

test("Ctrl+S does not star", () => {
  // Review Focus 4
  const map = { s: vi.fn() }
  render(<Keys map={map} />)
  press("s", window, { ctrlKey: true })
  press("s", window, { metaKey: true })
  press("s", window, { altKey: true })
  expect(map.s).not.toHaveBeenCalled()
  press("s")
  expect(map.s).toHaveBeenCalledTimes(1)
})

test("g s goes to Settings even when the view, which registered first, stars with s", () => {
  // a child's listener is added before its parent's: the view's map sees every key first
  const shell = { "g s": vi.fn() }
  const view = { s: vi.fn() }
  function Shell({ children }: { children: React.ReactNode }) {
    useShortcuts(shell)
    return children
  }
  function View() {
    useShortcuts(view)
    return null
  }
  render(<Shell><View /></Shell>)
  press("g"); press("s")
  expect(shell["g s"]).toHaveBeenCalledTimes(1)
  expect(view.s).not.toHaveBeenCalled()
  press("s")
  expect(view.s).toHaveBeenCalledTimes(1)
})

test("Enter on a focused link or button belongs to it", () => {
  const map = { Enter: vi.fn() }
  render(<><Keys map={map} /><a href="#x">Open in Gmail</a><button>Star</button></>)
  press("Enter", screen.getByText("Open in Gmail"))
  press("Enter", screen.getByText("Star"))
  expect(map.Enter).not.toHaveBeenCalled()
  press("Enter")
  expect(map.Enter).toHaveBeenCalledTimes(1)
})

test("g s still goes to Settings when the view registered after the Shell (after navigating)", () => {
  const shell = { "g s": vi.fn() }
  const view = { s: vi.fn() }
  function Shell() {
    useShortcuts(shell)
    return null
  }
  function View() {
    useShortcuts(view)
    return null
  }
  render(<><Shell /><View /></>) // siblings: Shell's listener is added first
  press("g"); press("s")
  expect(shell["g s"]).toHaveBeenCalledTimes(1)
  expect(view.s).not.toHaveBeenCalled()
})
