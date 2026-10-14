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
        {"mimeType": "text/plain", "body": {"data": b64("học phí " * 2_000)}},  # real words, not one blob
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


def test_html_links_survive_as_cleaned_urls():
    html = ('<p>New job: <a href="https://www.linkedin.com/comm/jobs/view/42/?otpToken=SECRET&trk=x">'
            'AI Intern</a></p><p><a href="https://x.com/unsub">Unsubscribe</a></p>')
    assert html_to_text(html) == "New job: AI Intern <https://www.linkedin.com/comm/jobs/view/42/>\n\nUnsubscribe"


def test_raw_url_as_link_text_is_replaced_by_the_clean_one():
    raw = "https://chatgpt.com/c/1?token=SECRET"
    assert html_to_text(f'<a href="{raw}">{raw}</a>') == "<https://chatgpt.com/c/1>"


def test_links_are_cleaned_before_the_body_is_truncated():
    junk = "https://www.linkedin.com/comm/jobs/view/1/?" + "&".join(f"trk{i}={'x' * 40}" for i in range(15))
    msg = make_message("j@linkedin.com", "me@gmail.com", "Jobs", [
        {"mimeType": "text/plain", "body": {"data": b64(f"{junk}\nDeadline Friday")}},
    ])
    assert len(junk) > 600
    body = build_mail_item(msg, UNI, max_body_chars=100).body_text
    assert body == "<https://www.linkedin.com/comm/jobs/view/1/>\nDeadline Friday"


def test_raw_html_in_a_plain_part_is_parsed_as_html():
    plain = '<p>New job</p><p><a href="https://www.linkedin.com/comm/jobs/view/7/?otpToken=SECRET">AI Intern</a></p><br>'
    msg = make_message("j@linkedin.com", "me@gmail.com", "Jobs", [
        {"mimeType": "text/plain", "body": {"data": b64(plain)}},
    ])
    assert build_mail_item(msg, UNI, 4000).body_text == "New job\n\nAI Intern <https://www.linkedin.com/comm/jobs/view/7/>"


def test_forwarded_recipient_lines_and_my_address_are_removed():
    body = ("---------- Forwarded message ---------\nFrom: Lecturer <l@uts.edu.au>\n"
            "To: Phuc <phuc.nguyen@student.example.edu.vn>, Mate <mate@student.example.edu.vn>\n"
            "Cc: All <k23@student.example.edu.vn>\n\nHi Phuc.Nguyen@Student.Example.edu.vn, lab moved."
            " Questions: tutor@uts.edu.au")
    msg = make_message("l@uts.edu.au", UNI[0], "Lab", [{"mimeType": "text/plain", "body": {"data": b64(body)}}])
    out = build_mail_item(msg, UNI, 4000, my_addresses=ME).body_text
    assert "mate@" not in out and "k23@" not in out
    assert "Hi <me>, lab moved." in out
    assert "tutor@uts.edu.au" in out  # a contact address is content


def test_prose_that_mentions_tags_is_not_parsed_as_html():
    plain = ("Week 3 lab: build a page with <div>, <span> and <p> elements.\nPut your CSS in a <style> block "
             "in the <head>.\nDeadline: Friday 17:00. Room changed to B1.203.")
    msg = make_message("l@uts.edu.au", UNI[0], "Lab", [{"mimeType": "text/plain", "body": {"data": b64(plain)}}])
    body = build_mail_item(msg, UNI, 4000).body_text
    assert "Deadline: Friday 17:00" in body and "B1.203" in body


def test_angle_autolinks_survive_in_html_looking_plain_parts():
    plain = ("<p>Hi,</p><p>Your course is ready.</p><div>JetBrains Academy</div>\n"
             "Start here: <https://www.jetbrains.com/academy/course/42/>\nQuestions? Support <support@jetbrains.com>")
    msg = make_message("a@jetbrains.com", "me@gmail.com", "Course",
                       [{"mimeType": "text/plain", "body": {"data": b64(plain)}}])
    body = build_mail_item(msg, UNI, 4000).body_text
    assert "<https://www.jetbrains.com/academy/course/42/>" in body and "support@jetbrains.com" in body


def test_forwarded_recipient_lists_are_removed_from_snippets_and_wrapped_html():
    html = ("<p>From: Lecturer &lt;l@uts.edu.au&gt;<br>Sent: Monday<br><b>To:</b> Phuc &lt;phuc.nguyen@student."
            "example.edu.vn&gt;; Mate Tran &lt;mate@student.example.edu.vn&gt;;\r\nBao Le &lt;bao.le@student."
            "example.edu.vn&gt;<br>Subject: Lab moved</p><p>Lab moved to B1.</p>")
    msg = make_message("l@uts.edu.au", UNI[0], "Lab", [{"mimeType": "text/html", "body": {"data": b64(html)}}])
    msg["snippet"] = ("---------- Forwarded message --------- From: Lecturer &lt;l@uts.edu.au&gt; Subject: Lab "
                      "moved To: Phuc &lt;phuc.nguyen@student.example.edu.vn&gt;, Mate &lt;mate@student.example.edu.vn&gt;")
    item = build_mail_item(msg, UNI, 4000, my_addresses=ME)
    for text in (item.body_text, item.snippet):
        assert "mate@" not in text and "bao.le@" not in text
    assert "Lab moved to B1." in item.body_text


def test_contact_address_after_to_colon_is_kept_outside_forwards():
    msg = make_message("hr@company.vn", "me@gmail.com", "Job", [{"mimeType": "text/plain", "body": {"data": b64(
        "We are hiring AI interns. Send your CV to: hr@company.vn before 30/10.")}}])
    assert "hr@company.vn" in build_mail_item(msg, UNI, 4000, my_addresses=ME).body_text


def test_odd_markup_that_crashes_the_stdlib_parser_is_survived():
    assert "Deadline" in html_to_text("<p>A <![ endif ]> Deadline Friday</p>")
    assert "Deadline" in html_to_text("<p>arr<![0] Deadline Friday</p>")


def test_text_after_the_last_tag_is_kept():
    assert html_to_text("<p>Hackathon</p>Deadline: 30/10") == "Hackathon\nDeadline: 30/10"
    # held back by the stdlib parser until close(): a trailing unfinished "&..."
    assert html_to_text("<p>Prize</p>Food & drinks &").endswith("Food & drinks &")


def test_corrupted_base64_body_is_empty_not_a_crash():
    from assistant.parser import decode_b64url
    assert decode_b64url("abcde") == ""        # impossible length
    assert decode_b64url("Xin chào") == ""     # not base64 at all


def test_prose_with_paired_tag_mentions_is_not_parsed_as_html():
    plain = ("Wrap text in <p></p>, use <div></div> and <span></span>; CSS goes in <style>.\n"
             "Deadline: 20/10/2026")
    msg = make_message("l@uts.edu.au", UNI[0], "Lab", [{"mimeType": "text/plain", "body": {"data": b64(plain)}}])
    assert "Deadline: 20/10/2026" in build_mail_item(msg, UNI, 4000).body_text


def test_huge_bodies_with_long_words_stay_fast():
    import time
    plain = " ".join("a" * 1999 for _ in range(100)) + " x@" + "a." * 995 + "1"
    msg = make_message("x@y.com", "me@gmail.com", "Big", [{"mimeType": "text/plain", "body": {"data": b64(plain)}}])
    start = time.perf_counter()
    build_mail_item(msg, UNI, 4000, my_addresses=ME)
    assert time.perf_counter() - start < 0.5  # was ~0.7 s per 200 KB (address regex retried every position)
