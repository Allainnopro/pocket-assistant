"""One downloaded email (raw bytes over IMAP) -> the shape parser.py reads.

The Gmail API used to deliver that shape; over IMAP this builds it, so the parser's
rules and tests stay exactly as they are. Pure: no network, no database.

Only text parts are kept: an attachment is never decoded or passed on. Downloads
stop at 2 MB, so the last part can be cut short, and real mail can be malformed in
any way at all: this function reads what it can and never raises because of the mail.
"""

from __future__ import annotations

import base64
import email
import email.policy
from email.message import Message

from .parser import html_to_text

SNIPPET_CHARS = 200
TEXT_TYPES = ("text/plain", "text/html")


def _clean(text: str) -> str:
    """Undecodable header bytes arrive as lone surrogates, which SQLite and JSON refuse."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _headers(message: Message) -> list[dict[str, str]]:
    headers = []
    for name, _ in message.raw_items():
        try:
            value = str(message[name])
        except Exception:  # a header the modern policy can't parse: keep the email, lose the header
            continue
        headers.append({"name": name, "value": _clean(value)})
    return headers


def _text_of(part: Message) -> str:
    data = part.get_payload(decode=True) or b""
    try:
        return data.decode(part.get_content_charset() or "utf-8", "replace")
    except LookupError:  # a charset Python doesn't know
        return data.decode("utf-8", "replace")


def _text_parts(message: Message) -> list[tuple[str, str]]:
    found = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_type() not in TEXT_TYPES:
            continue
        if part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        try:
            found.append((part.get_content_type(), _clean(_text_of(part))))
        except Exception:  # one unreadable part must not hold up the mail check for good
            continue
    return found


def _snippet(parts: list[tuple[str, str]]) -> str:
    plain = next((text for mime, text in parts if mime == "text/plain" and text.strip()), None)
    html = next((text for mime, text in parts if mime == "text/html" and text.strip()), "")
    text = plain if plain is not None else html_to_text(html)
    return " ".join(text.split())[:SNIPPET_CHARS]


def to_api_shape(raw: bytes, *, message_id: str, thread_id: str, labels: list[str], internal_date_ms: int) -> dict:
    message = email.message_from_bytes(raw, policy=email.policy.default)
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
