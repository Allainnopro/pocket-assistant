"""One downloaded email (raw bytes over IMAP) -> the shape parser.py reads.

The Gmail API used to deliver that shape; over IMAP this builds it, so the parser's
rules and tests stay exactly as they are. Pure: no network, no database.

Only text parts are kept: an attachment is never decoded or passed on, and neither is
an email attached to an email. Downloads stop at 2 MB, so the last part can be cut
short, and real mail can be malformed in any way at all: this reads what it can.

Choices, explained:
- The email is parsed with the compat32 policy, which only splits headers from
  values. The modern policy parses every header deeply, and a 62-byte header
  ("Content-Type: text/plain; a *") made it raise, while a 200 KB To: line kept it
  busy for minutes. Header values are decoded here instead, each cut to 8 KB.
- Text is decoded strictly, so a wrong label can't win: the declared charset (or the
  HTML's <meta> charset), then UTF-8, then CP949 for Korean mail sent unlabelled or
  mislabelled. Only then are errors replaced.
"""

from __future__ import annotations

import base64
import codecs
import email
import email.header
import email.policy
import re
from email.message import Message

from .parser import html_to_text

SNIPPET_CHARS = 200
SNIPPET_HTML_CHARS = 20_000  # the snippet needs the start of the mail, not all of it
HEADER_CHARS = 8_192
MAX_DEPTH = 30                # nested parts; real mail rarely passes 5
TEXT_TYPES = ("text/plain", "text/html")
# Labels that often hide UTF-8 or Windows' superset: try those first. EUC-KR needs no
# entry: when strict EUC-KR fails on CP949's extra syllables, CP949 is tried next.
CHARSETS = {"iso-8859-1": "cp1252", "latin-1": "cp1252", "latin1": "cp1252", "us-ascii": "", "ascii": ""}
META_CHARSET = re.compile(rb"""<meta[^>]{0,200}?charset\s*=\s*["']?([A-Za-z0-9_.:-]{1,40})""", re.IGNORECASE)


def _windows_names(name: str):
    """Python doesn't know the names Korean mailers use for CP949; email.header looks
    charsets up by name, so they are taught here."""
    return codecs.lookup("cp949") if name.replace("-", "_") in ("windows_949", "x_windows_949") else None


codecs.register(_windows_names)


def _clean(text: str) -> str:
    """Undecodable header bytes arrive as lone surrogates, which SQLite and JSON refuse."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _decode(data: bytes, declared: str | None) -> str:
    name = (declared or "").strip().lower()
    name = CHARSETS.get(name, name)
    tries = ["utf-8", name or "cp949"] if name in ("", "cp1252") else [name, "utf-8", "cp949"]
    for codec in tries:
        try:
            return data.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def _header_text(value: str) -> str:
    value = "".join(str(value)[:HEADER_CHARS].splitlines())
    raw = value.encode("ascii", "surrogateescape") if not value.isascii() else b""
    if raw:  # 8-bit bytes sent unencoded, against the rules but common
        try:
            return _decode(raw, None)
        except UnicodeEncodeError:
            pass
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # a broken encoded word: keep the header as sent
        return value


def _headers(message: Message) -> list[dict[str, str]]:
    return [{"name": name, "value": _clean(_header_text(value))} for name, value in message.raw_items()]


def _text_of(part: Message) -> str:
    data = part.get_payload(decode=True) or b""
    charset = part.get_content_charset()
    if not charset and part.get_content_type() == "text/html":
        found = META_CHARSET.search(data[:4096])
        charset = found.group(1).decode("ascii") if found else None
    return _decode(data, charset)


def _text_parts(message: Message) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(part: Message, depth: int) -> None:
        try:
            if depth > MAX_DEPTH:
                return
            # An attachment, an email attached to this one included, is never read
            if depth and (part.get_content_disposition() == "attachment" or part.get_filename()):
                return
            if part.is_multipart():
                for child in part.get_payload():
                    walk(child, depth + 1)
            elif part.get_content_type() in TEXT_TYPES:
                found.append((part.get_content_type(), _clean(_text_of(part))))
        except Exception:  # one unreadable part must not hold up the mail check for good
            return

    walk(message, 0)
    return found


def _snippet(parts: list[tuple[str, str]]) -> str:
    plain = next((text for mime, text in parts if mime == "text/plain" and text.strip()), None)
    html = next((text for mime, text in parts if mime == "text/html" and text.strip()), "")
    text = plain if plain is not None else html_to_text(html[:SNIPPET_HTML_CHARS])
    return " ".join(text.split())[:SNIPPET_CHARS]


def to_api_shape(raw: bytes, *, message_id: str, thread_id: str, labels: list[str], internal_date_ms: int) -> dict:
    message = email.message_from_bytes(raw, policy=email.policy.compat32)
    parts = _text_parts(message)
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(labels),
        "internalDate": str(internal_date_ms),
        "snippet": _snippet(parts),
        "payload": {
            "mimeType": message.get_content_type(),
            "headers": _headers(message),
            "parts": [{"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")}}
                      for mime, text in parts],
        },
    }
