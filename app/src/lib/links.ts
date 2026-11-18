/** A link we may put in an href. Opportunity URLs come from email text via the model, so a planted
 * `javascript:` link could otherwise run code in this page and read the launch password. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url.trim())
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null
  } catch {
    return null
  }
}
