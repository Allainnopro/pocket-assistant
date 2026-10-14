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
    is_outgoing,
    normalize_address,
    parse_recipients,
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
    assert classify_source("e.linkedin.com", {UNI[0]}, UNI) == "linkedin"


def test_forwarded_university_mail_detected_by_recipient():
    to = "Nguyen Hai Phuc <Phuc.Nguyen@student.example.edu.vn>"
    assert classify_source("example.edu.vn", parse_recipients(to)[0], UNI) == "university"


def test_unknown_sender_is_other():
    assert classify_source("shop.example.com", {"me@gmail.com"}, UNI) == "other"


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


ME = ("me@gmail.com", UNI[0])


def test_to_me_and_recipient_count_replace_the_recipient_list():
    msg = make_message(
        "Lecturer <l@example.edu.vn>",
        "A <a@x.com>, Phuc <PHUC.Nguyen@student.example.edu.vn>, b@x.com",
        "Lab 4",
        [{"mimeType": "text/plain", "body": {"data": b64("see you")}}],
    )
    msg["payload"]["headers"].append({"name": "Cc", "value": "b@x.com"})  # duplicate counted once
    item = build_mail_item(msg, UNI, 4000, my_addresses=ME)
    assert item.to_me is True  # case-insensitive match on my university address
    assert item.recipient_count == 3
    assert item.source == "university"
    assert not hasattr(item, "recipients")


def test_mail_to_a_list_is_not_to_me():
    msg = make_message("l@example.edu.vn", "k23-all@example.edu.vn", "Notice", [])
    assert build_mail_item(msg, UNI, 4000, my_addresses=ME).to_me is False


def test_to_me_is_unknown_without_my_addresses():
    msg = make_message("a@b.com", "me@gmail.com", "hi", [])
    assert build_mail_item(msg, UNI, 4000).to_me is None


def test_own_sent_mail_and_drafts_are_outgoing():
    msg = make_message("me@gmail.com", "l@example.edu.vn", "reply", [])
    assert not is_outgoing(msg)
    for label in ("SENT", "DRAFT"):
        msg["labelIds"] = ["INBOX", label]
        assert is_outgoing(msg)


def test_plus_tags_and_gmail_dots_are_still_me():
    assert normalize_address("Me.Name+devpost@GoogleMail.com") == "mename@gmail.com"
    assert normalize_address("phuc.nguyen+lab@student.example.edu.vn") == UNI[0]  # dots kept off Gmail
    msg = make_message("Devpost <hello@devpost.com>", "Me.Name+devpost@gmail.com", "hi", [])
    assert build_mail_item(msg, UNI, 4000, my_addresses=("me.name@gmail.com",)).to_me is True


def test_a_malformed_cc_does_not_wipe_out_to():
    msg = make_message("l@example.edu.vn", UNI[0], "Lab", [])
    msg["payload"]["headers"].append({"name": "Cc", "value": "a@x.com; b@x.com"})  # Outlook style
    item = build_mail_item(msg, UNI, 4000, my_addresses=ME)
    assert item.to_me is True
    assert item.source == "university"


def test_unreadable_header_makes_to_me_unknown_not_false():
    msg = make_message("x@y.com", "Office (CS, HCMUS) <cs@x.edu>", "hi", [])
    assert parse_recipients("Office (CS, HCMUS) <cs@x.edu>") == (set(), False)
    assert build_mail_item(msg, UNI, 4000, my_addresses=ME).to_me is None


def test_comma_in_a_display_name_does_not_inflate_the_count():
    msg = make_message("x@y.com", "Nguyen, Phuc <me@gmail.com>", "hi", [])
    item = build_mail_item(msg, UNI, 4000, my_addresses=ME)
    assert (item.to_me, item.recipient_count) == (True, 1)
