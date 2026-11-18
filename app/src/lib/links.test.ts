import { expect, test } from "vitest"
import { safeUrl } from "./links"

test("only http and https links are clickable", () => {
  // Opportunity URLs are written by the model from email text: untrusted
  expect(safeUrl("https://devpost.com/hackathons/1")).toBe("https://devpost.com/hackathons/1")
  expect(safeUrl("http://example.com/a?b=c")).toBe("http://example.com/a?b=c")
  expect(safeUrl("javascript:alert(document.cookie)")).toBeNull()
  expect(safeUrl(" JavaScript:alert(1)")).toBeNull()
  expect(safeUrl("data:text/html,<script>alert(1)</script>")).toBeNull()
  expect(safeUrl("vbscript:msgbox(1)")).toBeNull()
  expect(safeUrl("not a url")).toBeNull()
  expect(safeUrl(null)).toBeNull()
})
