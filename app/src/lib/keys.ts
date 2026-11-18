import { useEffect, useRef } from "react"

export const SEQUENCE_MS = 1000 // the second key of "g o" must follow within a second

/** Keys typed into a field belong to the field. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
}

/** Enter on a focused link or button opens or presses it. */
const ACTIVATES = "a, button, [role=button], [role=link]"

/** The sequence begun by one map's "g" is shared by every map, so "g s" in the Shell isn't the view's "s".
 * It answers exactly one following key (`ended`), however many listeners see that key. */
let chord: { key: string; at: number; started: KeyboardEvent; ended?: KeyboardEvent } | null = null

/** Single keys ("j", "Enter", "Escape", "/") and two-key sequences ("g o"). Never while typing, and never
 * with Ctrl, Alt or Meta held: Ctrl+S is the browser's, not "star". */
export function useShortcuts(map: Record<string, () => void>, enabled = true) {
  const current = useRef(map)
  current.current = map
  useEffect(() => {
    if (!enabled) return
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey || isTypingTarget(event.target)) return
      if (event.key === "Enter" && event.target instanceof Element && event.target.closest(ACTIVATES)) return
      const shortcuts = current.current
      if (chord?.started === event) return // another map began a sequence with this key
      if (chord && chord.ended === undefined) {
        if (Date.now() - chord.at < SEQUENCE_MS) chord.ended = event
        else chord = null // too late: this key stands on its own
      }
      if (chord?.ended === event) {
        const action = shortcuts[`${chord.key} ${event.key}`]
        if (action) {
          event.preventDefault()
          action()
        }
        return
      }
      if (Object.keys(shortcuts).some((combo) => combo.startsWith(`${event.key} `))) {
        chord = { key: event.key, at: Date.now(), started: event }
        return
      }
      const action = shortcuts[event.key]
      if (action) {
        event.preventDefault()
        action()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [enabled])
}
