"""Turn a raw Gmail API message into a clean MailItem.

This module is pure: no network, no database. That makes it the easiest part
to unit-test (see tests/test_parser.py), which matters because email formats
are where most real-world bugs hide.

How a Gmail message is shaped (format="full"):

    {
      "id": "...", "threadId": "...", "internalDate": "1760000000000",
      "snippet": "...", "labelIds": ["INBOX", ...],
      "payload": {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "From", "value": "..."}, ...],
        "parts": [
          {"mimeType": "text/plain", "body": {"data": "<base64url>"}},
          {"mimeType": "text/html",  "body": {"data": "<base64url>"}}
        ]
      }
    }

Parts can nest (multipart/mixed -> multipart/alternative -> text/plain),
so we walk the tree recursively.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser

from .models import MailItem

# Sender domain -> source tag. Matching is by suffix, so
# "jobalerts-noreply@linkedin.com" and "e.linkedin.com" both map to linkedin.
SOURCE_DOMAINS: dict[str, str] = {
    "linkedin.com": "linkedin",
    "devpost.com": "devpost",
    "mlh.io": "mlh",
    "devfolio.co": "devfolio",
    "unstop.com": "unstop",
    "kaggle.com": "kaggle",
    "itviec.com": "itviec",
    "topcv.vn": "topcv",
    "vietnamworks.com": "vietnamworks",
    "facebookmail.com": "facebook",
    "mail.instagram.com": "instagram",
}


# --------------------------------------------------------------------------- #
# Low-level decoding
# --------------------------------------------------------------------------- #

def decode_b64url(data: str) -> str:
    """Gmail encodes bodies as base64url, often WITHOUT '=' padding.

    Python's decoder insists on padding, so we add it back:
    base64 length must be a multiple of 4.
    """
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text, using only the standard library.

    Skips <script>/<style> content and inserts line breaks for block tags
    so the LLM in Phase 1 sees readable paragraphs, not one giant line.
    """

    _SKIP = {"script", "style", "head"}
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # turns &amp; into &
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return normalize_whitespace(extractor.text())


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)          # collapse runs of spaces
    text = re.sub(r"\n\s*\n+", "\n\n", text)     # at most one blank line
    return text.strip()


# --------------------------------------------------------------------------- #
# Walking the MIME tree
# --------------------------------------------------------------------------- #

def _collect_parts(part: dict, plain: list[str], html: list[str]) -> None:
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")

    # Skip attachments: they have a filename and we don't want PDF bytes as text
    if part.get("filename"):
        return

    if mime == "text/plain" and data:
        plain.append(decode_b64url(data))
    elif mime == "text/html" and data:
        html.append(decode_b64url(data))

    for child in part.get("parts", []) or []:
        _collect_parts(child, plain, html)


def extract_body(payload: dict) -> str:
    """Prefer text/plain (cleaner); fall back to stripped HTML.

    Many alert emails (LinkedIn, Devpost) are HTML-only, so the fallback
    is not an edge case, it's the common case.
    """
    plain: list[str] = []
    html: list[str] = []
    _collect_parts(payload, plain, html)

    if any(p.strip() for p in plain):
        return normalize_whitespace("\n\n".join(plain))
    if html:
        return html_to_text("\n".join(html))
    return ""


# --------------------------------------------------------------------------- #
# Headers and classification
# --------------------------------------------------------------------------- #

def get_header(payload: dict, name: str) -> str:
    """Header names are case-insensitive per the email RFCs."""
    target = name.lower()
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == target:
            return h.get("value", "")
    return ""


def sender_domain(from_header: str) -> str:
    _, address = parseaddr(from_header)
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def classify_source(domain: str, recipient_addrs: set[str], university_addresses: tuple[str, ...]) -> str:
    """Decide where an email came from. recipient_addrs come from parse_recipients.

    Order matters: a LinkedIn alert sent to your university address is still
    a LinkedIn alert, so platform domains win over the university check.
    """
    for known, tag in SOURCE_DOMAINS.items():
        if domain == known or domain.endswith("." + known):
            return tag

    if recipient_addrs & {normalize_address(a) for a in university_addresses}:
        return "university"

    return "other"


def normalize_address(addr: str) -> str:
    """One canonical form per mailbox, for "is this me?" comparisons.

    Case never matters, and a "+tag" suffix delivers to the same mailbox
    (Gmail, Google Workspace and Exchange all support it), so
    me+devpost@gmail.com is still me. Gmail also ignores dots in the local
    part and treats googlemail.com as gmail.com. Other domains keep their dots.
    """
    local, _, domain = addr.strip().lower().rpartition("@")
    if not local:
        return addr.strip().lower()
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def parse_recipients(*headers: str) -> tuple[set[str], bool]:
    """Normalized addresses from To/Cc header values, plus a "complete" flag.

    Each header is parsed on its own: Python's strict parser rejects a whole
    field when any part looks odd (Outlook's "a@x.com; b@x.com", a comma in a
    comment), and parsing To and Cc together would let one bad Cc wipe out
    To as well. complete=False means a non-empty header could not be read.
    Display-name fragments without an "@" are not counted as addresses.
    """
    found: set[str] = set()
    complete = True
    for header in headers:
        if not header or not header.strip():
            continue
        addrs = {normalize_address(a) for _, a in getaddresses([header]) if "@" in a}
        if not addrs and "@" in header:
            complete = False  # strict parse rejected it
        found |= addrs
    return found, complete


def _to_me(addrs: set[str], complete: bool, my_addresses: tuple[str, ...] | None) -> bool | None:
    if my_addresses is None:
        return None
    if addrs & {normalize_address(a) for a in my_addresses}:
        return True
    return False if complete else None  # an unreadable header might have named me


OUTGOING_LABELS = {"SENT", "DRAFT"}


def is_outgoing(message: dict) -> bool:
    """Mail the owner wrote. Not an opportunity, and not ours to keep a copy of.

    sync already excludes it in the Gmail query; this is the second guard in
    case a custom query in config.toml lets it through. Note: an email you
    send to yourself is labelled SENT too, so it is skipped as well.
    """
    return bool(OUTGOING_LABELS & set(message.get("labelIds", [])))


def build_mail_item(
    message: dict,
    university_addresses: tuple[str, ...],
    max_body_chars: int,
    my_addresses: tuple[str, ...] | None = None,
) -> MailItem:
    """my_addresses: every address that counts as "me" (Gmail + university).
    None means unknown, and to_me is stored as unknown too."""
    payload = message.get("payload", {})
    from_header = get_header(payload, "From")
    # To/Cc are read in memory only: needed to tag forwarded university mail,
    # then reduced to a yes/no and a count. Classmates' addresses never leave here.
    addrs, complete = parse_recipients(get_header(payload, "To"), get_header(payload, "Cc"))
    domain = sender_domain(from_header)

    # internalDate is milliseconds since epoch, as a string
    received = datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, tz=timezone.utc)

    return MailItem(
        message_id=message["id"],
        thread_id=message.get("threadId", ""),
        received_at=received.isoformat(),
        sender=from_header,
        sender_domain=domain,
        to_me=_to_me(addrs, complete, my_addresses),
        recipient_count=len(addrs),
        subject=get_header(payload, "Subject"),
        snippet=message.get("snippet", ""),
        body_text=extract_body(payload)[:max_body_chars],
        source=classify_source(domain, addrs, university_addresses),
        labels=list(message.get("labelIds", [])),
    )
