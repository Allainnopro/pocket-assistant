"""Parser tests with hand-built Gmail messages: no network, no account needed.

Run: pytest
"""

import base64

from assistant.parser import (
    build_mail_item,
    classify_source,
    decode_b64url,
    extract_body,
    html_to_text,
)

UNI = ("phuc.nguyen@student.example.edu.vn",)


def b64(text: str) -> str:
    # Mimic Gmail: base64url with the '=' padding stripped
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_message(from_, to, subject, parts, msg_id="m1"):
    return {
        "id": msg_id,
        "threadId": "t1",
        "internalDate": "1760000000000",
        "snippet": "preview",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": from_},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
            ],
            "parts": parts,
        },
    }


def test_decode_handles_missing_padding():
    assert decode_b64url(b64("Xin chào")) == "Xin chào"


def test_html_to_text_strips_tags_scripts_and_entities():
    html = "<html><head><style>p{}</style></head><body><p>AI Intern</p><script>x()</script><p>HCMC &amp; remote</p></body></html>"
    assert html_to_text(html) == "AI Intern\n\nHCMC & remote"


def test_prefers_plain_text_over_html():
    payload = {"parts": [
        {"mimeType": "text/plain", "body": {"data": b64("plain version")}},
        {"mimeType": "text/html", "body": {"data": b64("<p>html version</p>")}},
    ]}
    assert extract_body(payload) == "plain version"


def test_falls_back_to_html_in_nested_parts():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<div>Hackathon open</div>")}},
        ]},
    ]}
    assert extract_body(payload) == "Hackathon open"


def test_skips_attachments():
    payload = {"parts": [
        {"mimeType": "text/plain", "filename": "cv.txt", "body": {"data": b64("attachment")}},
        {"mimeType": "text/plain", "body": {"data": b64("real body")}},
    ]}
    assert extract_body(payload) == "real body"


def test_platform_domain_beats_university_recipient():
    assert classify_source("e.linkedin.com", UNI[0], UNI) == "linkedin"


def test_forwarded_university_mail_detected_by_recipient():
    to = "Nguyen Hai Phuc <Phuc.Nguyen@student.example.edu.vn>"
    assert classify_source("example.edu.vn", to, UNI) == "university"


def test_unknown_sender_is_other():
    assert classify_source("shop.example.com", "me@gmail.com", UNI) == "other"


def test_build_mail_item_end_to_end():
    msg = make_message(
        "Devpost <hello@devpost.com>",
        "me@gmail.com",
        "New hackathon: AI for Good",
        [{"mimeType": "text/html", "body": {"data": b64("<p>Prize $5,000</p>")}}],
    )
    item = build_mail_item(msg, UNI, max_body_chars=4000)
    assert item.source == "devpost"
    assert item.sender_domain == "devpost.com"
    assert item.body_text == "Prize $5,000"
    assert item.received_at.startswith("2025-10-09")


def test_body_is_truncated():
    msg = make_message("a@b.com", "me@gmail.com", "long", [
        {"mimeType": "text/plain", "body": {"data": b64("x" * 10_000)}},
    ])
    assert len(build_mail_item(msg, UNI, max_body_chars=100).body_text) == 100
