import { useEffect, useRef, useState, type CSSProperties } from "react"

/** In a list, only the first rows arrive one by one; the rest come with the last of them, so 200 rows never crawl in. */
export const ROW_CAP = 8

/** The arrival level of an element: `arrive` (index.css) waits 50 ms per level. */
export function step(i: number): CSSProperties {
  return { "--i": i } as CSSProperties
}

export function rowStep(base: number, row: number): CSSProperties {
  return step(base + Math.min(row, ROW_CAP))
}

/** Windows' "Animation effects" off, or no way to ask (tests): no motion. */
export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

/** A number that counts up (ease-out) from what it last showed to `value`; at once under reduced motion. */
export function useCountUp(value: number, ms = 600): number {
  const [shown, setShown] = useState(() => (prefersReducedMotion() ? value : 0))
  const last = useRef(shown)
  useEffect(() => {
    if (prefersReducedMotion()) {
      last.current = value
      setShown(value)
      return
    }
    const from = last.current
    let start: number | undefined
    let frame = requestAnimationFrame(function tick(now) {
      start ??= now
      const t = Math.min((now - start) / ms, 1)
      last.current = Math.round(from + (value - from) * (1 - (1 - t) ** 3))
      setShown(last.current)
      if (t < 1) frame = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(frame)
  }, [value, ms])
  return shown
}
