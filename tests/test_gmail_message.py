"""One downloaded email, in the shape the parser reads.

Run: pytest
"""

import base64

from assistant.gmail_message import to_api_shape
from assistant.parser import build_mail_item, decode_b64url, get_header

SAMPLE = (b"From: Devpost <hello@devpost.com>\r\nTo: me@gmail.com\r\nSubject: Hello\r\n"
          b"Content-Type: text/plain; charset=utf-8\r\n\r\nThe body text of this email.\r\n")


def shape(raw):
    return to_api_shape(raw, message_id="abc", thread_id="t1", labels=["INBOX"], internal_date_ms=1760000000000)


def bodies(result):
    return {p["mimeType"]: decode_b64url(p["body"]["data"]) for p in result["payload"]["parts"]}


def test_the_identifying_fields_are_passed_through():
    result = shape(SAMPLE)
    assert (result["id"], result["threadId"], result["labelIds"], result["internalDate"]) == (
        "abc", "t1", ["INBOX"], "1760000000000")


def test_headers_are_decoded_as_the_gmail_api_returns_them():
    raw = ("From: =?UTF-8?B?" + base64.b64encode("Phòng Đào tạo".encode()).decode() + "?= <pdt@example.edu>\r\n"
           "Subject: =?UTF-8?B?" + base64.b64encode("Thông báo".encode()).decode() + "?=\r\n\r\nx").encode()
    payload = shape(raw)["payload"]
    assert get_header(payload, "Subject") == "Thông báo"
    assert get_header(payload, "From") == "Phòng Đào tạo <pdt@example.edu>"


def test_the_result_builds_a_mail_item_through_the_real_parser():
    item = build_mail_item(shape(SAMPLE), ("me@uni.edu",), 4000, ("me@gmail.com",))
    assert (item.message_id, item.thread_id, item.subject, item.sender_domain) == ("abc", "t1", "Hello", "devpost.com")
    assert "body text" in item.body_text and "body text" in item.snippet
    assert item.received_at.startswith("2025-10-09")
    assert item.labels == ["INBOX"]


def test_quoted_printable_vietnamese_is_decoded():
    raw = (b"Subject: x\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
           b"Th=C3=B4ng b=C3=A1o h=E1=BB=8Dc ph=E1=BA=A7n")
    assert bodies(shape(raw))["text/plain"].strip() == "Thông báo học phần"


def test_a_legacy_charset_is_honoured():
    raw = "Subject: x\r\nContent-Type: text/plain; charset=windows-1252\r\n\r\n".encode() + "Café".encode("cp1252")
    assert "Café" in bodies(shape(raw))["text/plain"]


MIXED = (b"Subject: x\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=OUTER\r\n\r\n"
         b"--OUTER\r\nContent-Type: multipart/alternative; boundary=INNER\r\n\r\n"
         b"--INNER\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nplain words\r\n"
         b"--INNER\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>html words</p>\r\n--INNER--\r\n"
         b"--OUTER\r\nContent-Type: application/pdf; name=a.pdf\r\nContent-Disposition: attachment; filename=a.pdf\r\n"
         b"Content-Transfer-Encoding: base64\r\n\r\nU0VDUkVULVBERi1CWVRFUw==\r\n--OUTER--\r\n")


def test_a_multipart_email_keeps_both_text_parts_and_no_attachment():
    result = shape(MIXED)
    assert {p["mimeType"] for p in result["payload"]["parts"]} == {"text/plain", "text/html"}
    assert "SECRET-PDF-BYTES" not in str(result) and "U0VDUkVU" not in str(result)
    assert "plain words" in result["snippet"]


def test_a_text_file_attached_is_not_taken_for_the_body():
    raw = (b"Subject: x\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: text/plain\r\n\r\nthe email\r\n"
           b"--B\r\nContent-Type: text/plain; name=n.txt\r\nContent-Disposition: attachment; filename=n.txt\r\n\r\n"
           b"ATTACHED FILE\r\n--B--\r\n")
    assert "ATTACHED FILE" not in str(bodies(shape(raw)))


def test_an_html_only_email_has_a_text_snippet():
    raw = b"Subject: x\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<html><body><p>AI Intern</p></body></html>"
    result = shape(raw)
    assert "AI Intern" in result["snippet"] and "<" not in result["snippet"]
    assert "<p>AI Intern</p>" in bodies(result)["text/html"]


def test_the_snippet_is_at_most_200_characters():
    raw = b"Subject: x\r\nContent-Type: text/plain\r\n\r\n" + b"word " * 500
    assert len(shape(raw)["snippet"]) <= 200


def test_a_message_truncated_at_2_mb_converts():
    cut = MIXED[:MIXED.index(b"html words") + 4]  # in the middle of a part, no closing boundary
    assert "plain words" in bodies(shape(cut))["text/plain"]


def test_a_broken_email_still_converts():
    for raw in (b"", b"not an email at all", b"Content-Type: text/plain; charset=klingon\r\n\r\nabc\xff",
                b"Subject: =?bad?Q?x?=\r\nContent-Type: multipart/mixed\r\n\r\nno boundary",
                b"Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!not base64!!!"):
        result = shape(raw)
        assert result["id"] == "abc"
        build_mail_item(result, (), 4000, ("me@gmail.com",))  # and the parser takes it


def test_an_unreadable_part_is_left_out(monkeypatch):
    import assistant.gmail_message as gmail_message

    def boom(part):
        raise ValueError("odd")

    monkeypatch.setattr(gmail_message, "_text_of", boom)
    assert shape(SAMPLE)["payload"]["parts"] == []
