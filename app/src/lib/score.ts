// A score is always shown as its number; the 3-segment bar beside it reads high / medium / low at a glance.
export const HIGH = 75
export const MEDIUM = 50

export function segments(score: number): 1 | 2 | 3 {
  if (score >= HIGH) return 3
  if (score >= MEDIUM) return 2
  return 1
}
