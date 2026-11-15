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


# --- review fixes: hostile mail, attached emails, charsets ----------------------------------------

import time  # noqa: E402

import pytest  # noqa: E402

KOREAN = "가천대학교 장학금 신청 마감: 11월 30일 똠방각하"  # the last word needs CP949's extra syllables
VIETNAMESE = "Thông báo"  # every byte of its UTF-8 is also valid CP1252: a wrong guess shows


def text_mail(body: bytes, content_type: str = "text/plain", charset: str | None = None, extra: str = "") -> bytes:
    params = f"; charset={charset}" if charset else ""
    return (f"From: a@b.c\r\nTo: me@gmail.com\r\nSubject: s\r\n{extra}Content-Type: {content_type}{params}\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n").encode() + body


@pytest.mark.parametrize("header", ["Content-Type: text/plain; a *", "Content-Type: text/plain; charset*",
                                    'Content-Disposition: inline; filename *'])
def test_a_parameter_with_no_value_doesnt_stop_the_mail(header):
    raw = f"From: a@b.c\r\nSubject: s\r\n{header}\r\n\r\nThe body text.\r\n".encode()
    assert "The body text." in bodies(shape(raw)).get("text/plain", "")


@pytest.mark.parametrize("piece, times", [("<a ", 600_000), ("<!--", 450_000), ("<", 1_800_000), ("<a b='", 300_000)])
def test_hostile_html_converts_in_a_moment(piece, times):
    raw = text_mail((piece * times).encode(), "text/html", "utf-8")
    started = time.monotonic()
    result = shape(raw)
    build_mail_item(result, (), 4000, ("me@gmail.com",))
    assert time.monotonic() - started < 5


def test_a_huge_header_converts_in_a_moment():
    raw = ("From: a@b.c\r\nTo: " + "," * 200_000 + "\r\nSubject: " + "x" * 1_000_000 + "\r\n\r\nbody").encode()
    started = time.monotonic()
    build_mail_item(shape(raw), (), 4000, ("me@gmail.com",))
    assert time.monotonic() - started < 5


def test_an_attached_email_is_not_taken_for_the_body():
    inner = b"From: x@y.z\r\nSubject: old\r\nContent-Type: text/plain\r\n\r\nSECRET ATTACHED BODY\r\n"
    raw = (b"From: a@b.c\r\nSubject: s\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: text/plain\r\n\r\nvisible note\r\n"
           b"--B\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename=old.eml\r\n\r\n"
           + inner + b"--B--\r\n")
    result = shape(raw)
    assert bodies(result) == {"text/plain": "visible note"}
    assert "SECRET" not in build_mail_item(result, (), 4000, ()).body_text


def test_an_email_forwarded_inline_is_still_read():
    inner = b"From: x@y.z\r\nSubject: old\r\nContent-Type: text/plain\r\n\r\nthe forwarded text\r\n"
    raw = (b"From: a@b.c\r\nSubject: s\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: message/rfc822\r\n\r\n" + inner + b"--B--\r\n")
    assert "the forwarded text" in bodies(shape(raw))["text/plain"]


@pytest.mark.parametrize("charset", ["windows-949", "x-windows-949", "euc-kr", "ks_c_5601-1987", None])
def test_korean_mail_reads_whatever_its_label(charset):
    assert bodies(shape(text_mail(KOREAN.encode("cp949"), charset=charset)))["text/plain"] == KOREAN


@pytest.mark.parametrize("charset", ["us-ascii", "iso-8859-1", None])
def test_utf8_mail_mislabelled_as_western_reads_correctly(charset):
    assert bodies(shape(text_mail(VIETNAMESE.encode(), charset=charset)))["text/plain"] == VIETNAMESE


def test_real_western_mail_still_reads_as_western():
    assert bodies(shape(text_mail("café déjà vu".encode("cp1252"), charset="iso-8859-1")))["text/plain"] == \
        "café déjà vu"


def test_an_html_part_with_its_charset_only_in_a_meta_tag():
    html = '<html><head><meta charset="windows-1252"></head><body><p>café déjà vu</p></body></html>'
    item = build_mail_item(shape(text_mail(html.encode("cp1252"), "text/html")), (), 4000, ())
    assert "café déjà vu" in item.body_text


def test_korean_and_raw_8bit_headers_are_decoded():
    encoded = base64.b64encode(KOREAN.encode("cp949")).decode()
    raw = (f"From: =?windows-949?B?{encoded}?= <a@b.c>\r\nSubject: =?x-windows-949?B?{encoded}?=\r\n\r\nx").encode()
    payload = shape(raw)["payload"]
    assert get_header(payload, "Subject") == KOREAN and get_header(payload, "From") == f"{KOREAN} <a@b.c>"
    raw8 = b"From: a@b.c\r\nSubject: " + VIETNAMESE.encode() + b"\r\nX-K: " + KOREAN.encode("cp949") + b"\r\n\r\nx"
    payload = shape(raw8)["payload"]
    assert get_header(payload, "Subject") == VIETNAMESE and get_header(payload, "X-K") == KOREAN


@pytest.mark.parametrize("folded, expected", [(b"one\r\n two", "one two"), (b"one\r\n two =?utf-8?q?three?=", "one two three")])
def test_a_folded_header_reads_as_one_line(folded, expected):
    raw = b"From: a@b.c\r\nSubject: " + folded + b"\r\n\r\nx"
    assert get_header(shape(raw)["payload"], "Subject") == expected

