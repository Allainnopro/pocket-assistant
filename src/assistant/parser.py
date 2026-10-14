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
import html as html_lib
from html.parser import HTMLParser

from .links import ADDRESS, URL_IN_TEXT, clean_text_urls, drop_wrapped_secrets, link_marker, scrub_residue
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
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except ValueError:  # corrupted data (binascii.Error is a ValueError): treat as empty
        return ""


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text, using only the standard library.

    Skips <script>/<style> content and inserts line breaks for block tags
    so the LLM in Phase 1 sees readable paragraphs, not one giant line.
    Each link gets " <cleaned href>" after its text (see links.py). Text is
    kept raw here and cleaned once by the caller, after all pieces are
    joined: a URL split by an inline tag such as <wbr> must rejoin before it
    can be cleaned, or the tail would slip through with its tokens.
    """

    _SKIP = {"script", "style", "head"}
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # turns &amp; into &

    def reset(self) -> None:
        # Our own state lives here, so a retry after a parse error starts clean
        super().reset()
        self._parts: list[str] = []
        self._skip_depth = 0
        self._href: str | None = None
        self._anchor_text: list[str] | None = None  # not None while inside <a>

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "a" and self._anchor_text is not None:
            self._parts.append(link_marker("".join(self._anchor_text), self._href))
            self._href, self._anchor_text = None, None
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        # In HTML a raw line break is just a space; only block tags break lines.
        # (Outlook wraps long "To:" lists in its source; they must stay one line.)
        data = data.replace("\r", " ").replace("\n", " ")
        self._parts.append(data)
        if self._anchor_text is not None:
            self._anchor_text.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    return clean_text(_feed_safely(_TextExtractor(), html).text())


def _feed_safely(parser, html: str):
    """The stdlib HTMLParser raises AssertionError on some odd markup ("<![ endif ]>").
    Neutralize "<![" and retry; as a last resort, feed the text with tags stripped."""
    for attempt in (html, html.replace("<![", "&lt;!["), re.sub(r"<[^>]*>", " ", html)):
        try:
            parser.reset()
            parser.feed(attempt)
            parser.close()
            return parser
        except AssertionError:
            continue
    return parser


def clean_text(text: str) -> str:
    """Clean every URL in one pass (each kept once), sweep leftovers, tidy spaces."""
    text = drop_wrapped_secrets(text)  # "verify?otp=\n482913": the code lost to a line wrap
    return normalize_whitespace(scrub_residue(clean_text_urls(text, set())))


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)          # collapse runs of spaces
    text = re.sub(r" ?\n ?", "\n", text)          # no spaces hugging line breaks
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


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def reset(self) -> None:
        super().reset()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href") if tag == "a" else None
        if href:
            self.hrefs.append(href)


def raw_urls(payload: dict) -> list[str]:
    """Every link in a message exactly as sent, for `assistant links` only.

    Never stored: the audit reduces these to hosts and parameter names."""
    plain: list[str] = []
    html: list[str] = []
    _collect_parts(payload, plain, html)
    urls = [m.group(2) for text in plain for m in URL_IN_TEXT.finditer(text)]
    for doc in html:
        urls += _feed_safely(_HrefCollector(), doc).hrefs
    return urls


def extract_body(payload: dict) -> str:
    """Prefer text/plain (cleaner); fall back to stripped HTML.

    Many alert emails (LinkedIn, Devpost) are HTML-only, so the fallback
    is not an edge case, it's the common case. Links are cleaned here, before
    build_mail_item truncates the body, so tracking junk can't eat the budget.
    """
    plain: list[str] = []
    html: list[str] = []
    _collect_parts(payload, plain, html)

    if any(p.strip() for p in plain):
        # Some senders (LinkedIn, JetBrains) put raw HTML in the text/plain part
        return "\n\n".join(html_to_text(_protect_autolinks(p)) if _looks_like_html(p) else clean_text(p)
                           for p in plain if p.strip())
    if html:
        return html_to_text("\n".join(html))
    return ""


HTML_STRUCTURE = re.compile(r"<(html|body|!doctype)\b|<a\s+href=", re.I)
CLOSING_TAG = re.compile(r"</(p|div|li|td|tr|table|a|span|strong|b|em|h[1-6])>", re.I)
ANGLE_AUTOLINK = re.compile(r"<((?:https?://|mailto:)[^<>\s]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)>", re.I)


def _looks_like_html(text: str) -> bool:
    """Real markup, not prose that mentions tags ("use <div>, <span> and <p>")."""
    head = text[:20_000]
    return bool(HTML_STRUCTURE.search(head)) or len(CLOSING_TAG.findall(head)) >= 5


def _protect_autolinks(text: str) -> str:
    """'<https://x>' and 'Name <a@b.com>' in a plain part are text, not tags.
    An unclosed <style>/<script>/<head> mentioned in prose would swallow the
    rest of the email, so those are neutralized too."""
    text = ANGLE_AUTOLINK.sub(r" \1 ", text)
    for tag in ("style", "script", "head", "title"):
        if not re.search(rf"</{tag}\s*>", text, re.I):
            text = re.sub(rf"<({tag})\b", r"&lt;\1", text, flags=re.I)
    return text


# Forwarded-message header lines listing recipients ("To: A <a@x>; B <b@x>").
RECIPIENT_LINE = re.compile(r"(?im)^[ \t>*]*(to|cc|bcc|đến|tới|người nhận|gửi đến)\s*:[^\n]*@[^\n]*$")
_ADDR_ITEM = r"(?:[^<>,;\n:]{0,80}<[^<>\s]+@[^<>\s]+>|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
FORWARD_MARKER = re.compile(
    r"(?is)forwarded message|original message|thư được chuyển tiếp"
    r"|\bfrom:.{0,300}?\b(sent|date|subject|ngày|tiêu đề)\s*:"
)
INLINE_RECIPIENTS = re.compile(
    rf"(?i)\b(?:to|cc|bcc|đến|tới)\s*:\s*{_ADDR_ITEM}(?:\s*[,;]\s*{_ADDR_ITEM})*"
)


def redact_people(text: str, my_addresses: tuple[str, ...] | None) -> str:
    """Remove other people's address lists and the owner's own address.

    Forwarded university mail repeats its header block inside the body, and
    its To:/Cc: lines can list a whole class: those lines go. The owner's own
    address becomes "<me>". Other single addresses stay, because "send your
    CV to hr@company.vn" is exactly what extraction needs.
    """
    text = RECIPIENT_LINE.sub("", text)
    if FORWARD_MARKER.search(text):
        # The same list inside one line (Gmail snippets). Only within a forwarded
        # header block: "Send your CV to: hr@company.vn" elsewhere is content.
        text = INLINE_RECIPIENTS.sub("", text)
    if my_addresses:
        mine = {normalize_address(a) for a in my_addresses}
        text = ADDRESS.sub(lambda m: "<me>" if normalize_address(m.group(0)) in mine else m.group(0), text)
    return text


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
        # Gmail's snippet is a raw preview of the start of the email: clean it too
        snippet=redact_people(clean_text(html_lib.unescape(message.get("snippet", ""))), my_addresses),
        body_text=redact_people(extract_body(payload), my_addresses)[:max_body_chars],
        source=classify_source(domain, addrs, university_addresses),
        labels=list(message.get("labelIds", [])),
    )
