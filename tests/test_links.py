"""Link-cleaning tests. URL shapes mirror the owner's real mail (hosts and
parameter names from the survey) and the reviewers' leak findings; every
value here is made up.

Run: pytest
"""

import random
import secrets
import string
import time

from assistant.links import audit, clean_text_urls, clean_url, link_marker, looks_random, scrub_residue
from assistant.parser import clean_text, html_to_text

LINKEDIN_JOB = (
    "https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc%3D%3D&refId=xyz"
    "&lipi=urn%3Ali%3Apage&midToken=AQH123&midSig=0sig&trk=eml-job&trkEmail=eml-x"
    "&eid=e1&otpToken=SECRET-ONE-TIME-LOGIN"
)


# --- survey-based rules -----------------------------------------------------

def test_linkedin_keeps_the_job_path_and_drops_the_login_token():
    assert clean_url(LINKEDIN_JOB).url == "https://www.linkedin.com/comm/jobs/view/4012345678/"


def test_linkedin_search_links_keep_their_search_but_nothing_else():
    url = ("https://www.linkedin.com/comm/jobs/search?keywords=AI%20Intern&geoId=104195383"
           "&f_TPR=r86400&trk=eml&otpToken=SECRET&savedSearchId=9")
    assert clean_url(url).url == (
        "https://www.linkedin.com/comm/jobs/search?keywords=AI%20Intern&geoId=104195383&f_TPR=r86400"
    )


def test_facebook_notification_link_is_rebuilt_from_its_hidden_destination():
    url = ("https://www.facebook.com/n/?groups%2F1234567%2Fpermalink%2F7654321%2F"
           "&aref=1&medium=email&mid=abc&bcode=2.x&n_m=me%40gmail.com&lloc=cta")
    assert clean_url(url).url == "https://www.facebook.com/groups/1234567/permalink/7654321/"


def test_generic_tracking_and_secret_params_go_but_real_ones_stay():
    url = ("https://chatgpt.com/c/123?entry_id=9&utm_source=e&utm_term=t&login_hint=me%40x.com"
           "&token=SECRET&auth_modal=1&deep_link_id=7")
    assert clean_url(url).url == "https://chatgpt.com/c/123?entry_id=9&deep_link_id=7"
    assert clean_url("https://play.google.com/store/apps/details?id=com.linkedin.android&referrer=x").url == (
        "https://play.google.com/store/apps/details?id=com.linkedin.android"
    )


def test_click_trackers_are_dropped_entirely():
    for url in ("https://click.discord.com/ls/click?upn=u001.PAYLOAD",
                "https://r.openai.com/tr/cl/abc?upn=PAYLOAD",
                "https://email.example.com/c/x?upn=PAYLOAD",          # upn on a branded host
                "https://cl.exct.net/?qs=SECRETqs0123abcd"):           # Salesforce
        assert clean_url(url).reason == "tracker", url


def test_non_web_links_are_dropped():
    for url in ("mailto:hi@x.com", "javascript:alert(1)", "tg://resolve?domain=x", "#top", "/relative"):
        assert clean_url(url).url is None


def test_port_kept_ipv6_bracketed_credentials_dropped():
    assert clean_url("https://user:pass@Example.com:8443/a?utm_source=x").url == "https://example.com:8443/a"
    assert clean_url("http://[::1]:8080/a?x=1").url == "http://[::1]:8080/a?x=1"
    assert clean_url("https://example.com:99999/a").url is None


def test_kept_parameters_stay_byte_identical():
    assert clean_url("https://example.com/index.php?/archives/123").url == "https://example.com/index.php?/archives/123"
    assert clean_url("https://example.com/a?x=%E9t%E9").url == "https://example.com/a?x=%E9t%E9"


# --- reviewer findings: nested URLs and wrappers ------------------------------

def test_redirect_wrappers_are_unwrapped_and_the_inside_cleaned():
    assert clean_url("https://www.google.com/url?q=https://example.com/join?token%3DSECRET&sa=D&usg=x").url == (
        "https://example.com/join"
    )
    safelinks = ("https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fportal.uni.edu.vn%2F"
                 "events%3Ftoken%3DSECRET&data=05%7C02%7Cme%40student.uni.edu.vn%7Cx&sdata=Ab12%3D&reserved=0")
    assert clean_url(safelinks).url == "https://portal.uni.edu.vn/events"


def test_a_url_inside_any_parameter_is_cleaned_too():
    v = clean_url("https://example.com/go?next=https%3A%2F%2Fx.com%2Fa%3Ftoken%3DSECRET%26page%3D2")
    assert v.url == "https://example.com/go?next=https%3A%2F%2Fx.com%2Fa%3Fpage%3D2"


# --- reviewer findings: values judged, not just names -------------------------

def test_values_that_identify_the_reader_are_dropped_whatever_the_name():
    for query in ("ck_subscriber_id=SUBID123", "_kx=SECRETkx.SUBID", "to=me%40gmail.com",
                  "u=me%40gmail.com", "code=SECRETCODE", "pwd=SECRET", "oobCode=Xy7_SECRETCODE",
                  "j=eyJhbGciOiJIUzI1NiJ9.SECRET", "x=" + secrets.token_urlsafe(24)):
        url = clean_url(f"https://example.com/page?keep=1&{query}").url
        assert url == "https://example.com/page?keep=1", query


def test_html_escaped_ampersands_in_plain_text_are_understood():
    text = "Apply https://jobs.example.com/view/42?id=42&amp;mc_eid=SUBID&amp;mkt_tok=SECRET&amp;upn=SECRET"
    assert clean_text_urls(text, set()) == "Apply"  # upn means a tracker: dropped whole, with its space
    text = "Apply https://jobs.example.com/view/42?id=42&amp;mc_eid=SUBID&amp;mkt_tok=SECRET"
    assert clean_text_urls(text, set()) == "Apply <https://jobs.example.com/view/42?id=42>"


# --- reviewer findings: random-looking paths ----------------------------------

def test_tokens_are_random_but_slugs_and_job_ids_are_not():
    assert looks_random("3f786850e387550fdab836ed7e6dc881de23001b")           # hex hash
    assert looks_random("Ab3_kX9-qL2_mN8-pQ4rS7tU1vW0xYz")                     # base64url with separators
    rng = random.Random(42)  # deterministic base64url tokens, 43 chars like token_urlsafe(32)
    alphabet = string.ascii_letters + string.digits + "-_"
    tokens = ["".join(rng.choices(alphabet, k=43)) for _ in range(1000)]
    assert sum(map(looks_random, tokens)) >= 990
    for slug in ("AI-for-Good-Hackathon-2026-Registration", "covid19-ai-hackathon-2026-final",
                 "VietnamAIHackathon2026", "GoogleDeveloperGroupsHCMC", "web3-ai-gpt4-hack-2026",
                 "machine-learning-engineer-intern-4012345678", "4012345678901234567890"):
        assert not looks_random(slug), slug


def test_job_board_uuids_stay_but_reset_link_uuids_go():
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    assert clean_url(f"https://jobs.lever.co/acme/{uuid}").url == f"https://jobs.lever.co/acme/{uuid}"
    assert clean_url(f"https://app.example.com/reset-password/{uuid}").url is None


def test_content_ids_on_known_sites_are_kept():
    form = "https://docs.google.com/forms/d/e/1FAIpQLSc_3x9Yk4Lm2Np7Qr5St8Uv1Wx3Yz6Ab9Cd2Ef5Gh8Ij/viewform"
    assert clean_url(form).url == form


def test_facebook_rebuilt_destination_is_checked_too():
    url = "https://www.facebook.com/n/?recover/code/AbCdEf0123456789AbCdEf0123456789AbCd&aref=1"
    assert clean_url(url).url is None


def test_email_address_in_a_path_drops_the_link():
    assert clean_url("https://example.com/u/me%40gmail.com/confirm").url is None


# --- reviewer findings: text cleaning ------------------------------------------

def test_url_cut_by_a_space_or_line_break_leaves_no_tail():
    unsub = ("Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe?x=1&n=the Team"
             "&lipi=urn%3Ali%3Apage%3Ax&midToken=SECRETMID&otpToken=SECRET\nHelp: ok")
    assert clean_text(unsub) == "Unsubscribe:\nHelp: ok"
    assert clean_text("Verify: https://example.com/verify?id=1&\ntoken=SECRET") == "Verify: <https://example.com/verify?id=1>"


def test_url_split_by_an_inline_tag_is_cleaned_whole():
    out = html_to_text('<p>Link: https://www.linkedin.com/comm/jobs/view/1/?trk=x&amp;<wbr>otpToken=SECRET</p>')
    assert out == "Link: <https://www.linkedin.com/comm/jobs/view/1/>"
    out = html_to_text('<a href="https://x.com/reset">Reset here: https://x.com/reset?token=SECRET</a>')
    assert out == "Reset here: <https://x.com/reset>"


def test_visible_real_url_survives_a_tracker_href():
    html = ('<p>Register: <a href="https://abc.us1.list-manage.com/track/click?u=1&id=2&e=3">'
            'https://devpost.com/hackathons/ai-2026</a></p>')
    assert html_to_text(html) == "Register: <https://devpost.com/hackathons/ai-2026>"


def test_bare_www_urls_are_cleaned():
    assert clean_text("Reset: www.example.com/reset?token=SECRET&lang=vi") == "Reset: <http://www.example.com/reset?lang=vi>"


def test_footer_matches_whole_labels_not_words_in_titles():
    assert link_marker("Unsubscribe", "https://x.com/u?id=1") == ""
    assert link_marker("Manage your email preferences", "https://x.com/p") == ""
    assert link_marker("Join the Privacy & AI Hackathon", "https://privacyhack.devpost.com/") == (
        " <https://privacyhack.devpost.com/>"
    )
    assert clean_text("Privacy hackathon: https://devpost.com/software/privacy-guard deadline Fri") == (
        "Privacy hackathon: <https://devpost.com/software/privacy-guard> deadline Fri"
    )


def test_each_url_once_and_punctuation_kept():
    text = f"See {LINKEDIN_JOB}. Again: {LINKEDIN_JOB}, or <https://x.com/a?utm_source=e>, thanks"
    assert clean_text(text) == (
        "See <https://www.linkedin.com/comm/jobs/view/4012345678/>. Again:, or <https://x.com/a>, thanks"
    )


def test_balanced_parentheses_stay_in_the_url():
    assert clean_text("Read https://en.wikipedia.org/wiki/Python_(programming_language) now") == (
        "Read <https://en.wikipedia.org/wiki/Python_(programming_language)> now"
    )
    assert clean_text("(see https://x.com/a)") == "(see <https://x.com/a>)"


def test_app_route_fragments_kept_secret_fragments_dropped():
    assert clean_url("https://app.example.com/#/events/123").url == "https://app.example.com/#/events/123"
    assert clean_url("https://x.com/cb#access_token=SECRET&x=1").url == "https://x.com/cb"


def test_trailing_punctuation_cannot_stall_sync():
    start = time.perf_counter()
    clean_text_urls("see https://x.com/a" + "." * 1_000_000, set())
    assert time.perf_counter() - start < 2  # was ~22 s (quadratic)


def test_residue_sweep_leaves_normal_prose_alone():
    prose = "Deadline 30/10, prize 5,000$ & mentoring. Score = 92. Thông báo học phí HK1/2026-2027."
    assert scrub_residue(prose) == prose


# --- the audit never prints values -------------------------------------------

def test_audit_reports_names_never_values():
    report = audit([LINKEDIN_JOB, "https://x.com/a?keep=1&utm_source=SECRETVALUE&blank="])
    assert "otpToken" in repr(report) and "utm_source" in repr(report)
    assert "SECRET" not in repr(report)
    hosts = {r["host"]: r for r in report}
    assert hosts["x.com"]["kept_params"] == {"keep": 1, "blank": 1}


def test_audit_masks_bare_keys_that_are_really_destinations():
    report = audit(["https://www.facebook.com/n/?nguyenvana2003&aref=1&n_m=me%40x.com",
                    "https://www.facebook.com/n/?nguyen.van.a.123456&aref=1"])
    flat = repr(report)
    assert "nguyenvana2003" not in flat and "nguyen.van.a" not in flat and "me@x.com" not in flat
    assert report[0]["dropped_params"]["<bare key>"] == 2


# --- round-2 review findings ---------------------------------------------------

def test_links_glued_to_punctuation_survive_the_sweep():
    assert clean_text("Register (https://example.com/event?id=42&lang=vi) by Friday") == (
        "Register (<https://example.com/event?id=42&lang=vi>) by Friday"
    )
    assert clean_text("Link đăng ký:https://example.com/event?id=42&lang=vi") == (
        "Link đăng ký:<https://example.com/event?id=42&lang=vi>"
    )
    assert html_to_text('<p>Recording (<a href="https://youtu.be/dQw4w9WgXcQ">https://youtu.be/dQw4w9WgXcQ</a>)</p>') == (
        "Recording (<https://youtu.be/dQw4w9WgXcQ>)"
    )
    assert clean_text("Apply: [Backend Intern](https://jobs.example.com/view?id=42&lang=vi) - closes Fri") == (
        "Apply: [Backend Intern](<https://jobs.example.com/view?id=42&lang=vi>) - closes Fri"
    )


def test_vietnamese_and_spaced_paths_are_titles_not_tokens():
    for url in ("https://uni.edu.vn/files/Th%C3%B4ng%20b%C3%A1o%20h%E1%BB%8Dc%20ph%C3%AD.pdf",
                "https://uni.edu.vn/files/Thong%20bao%20hoc%20phi.pdf",
                "https://vi.wikipedia.org/wiki/Th%C3%A0nh_ph%E1%BB%91_H%E1%BB%93_Ch%C3%AD_Minh"):
        cleaned = clean_url(url).url
        assert cleaned == url, url
        assert clean_url(cleaned).url == cleaned  # idempotent
    assert html_to_text('<a href="https://example.com/tin-tuc/đăng-ký">Đăng ký</a>') == (
        "Đăng ký <https://example.com/tin-tuc/%C4%91%C4%83ng-k%C3%BD>"
    )


def test_reset_tokens_on_trusted_sites_are_still_dropped():
    token = "Ab3kX9qL2mN8pQ4rS7tU1vW0xYz5CdEf"
    for url in (f"https://github.com/password_reset/{token}",
                f"https://www.figma.com/team_invite/redeem/{token}",
                f"https://www.notion.so/invite/{token}"):
        assert clean_url(url).url is None, url


def test_uuid_tokens_only_survive_on_job_boards():
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    assert clean_url(f"https://jobs.lever.co/acme/{uuid}").url
    for path in ("account/activate", "set-password", "recover", "events"):
        assert clean_url(f"https://app.example.com/{path}/{uuid}").url is None, path


def test_tokens_in_fragments_are_dropped():
    for url in ("https://app.example.com/#/reset-password/550e8400-e29b-41d4-a716-446655440000",
                "https://app.example.com/#/verify/Ab3_kX9-qL2_mN8-pQ4rS7tU1vW0xYz",
                "https://app.example.com/#/confirm/me@gmail.com",
                "https://app.example.com/magic#Ab3kX9qL2mN8pQ4rS7tU1vW0xYzAb3kX9qL2"):
        assert "#" not in (clean_url(url).url or ""), url


def test_service_specific_reader_ids_are_dropped():
    assert clean_url("https://example.substack.com/p/my-post?r=2bjtip&utm_medium=email").url == (
        "https://example.substack.com/p/my-post"
    )
    assert clean_url("https://mailchi.mp/brand/october-newsletter?e=1a2b3c4d5e").url == (
        "https://mailchi.mp/brand/october-newsletter"
    )
    assert clean_url("https://example.com/v?pin=482913&s=A1b2C3d4e5&page=2").url == "https://example.com/v?page=2"


def test_teams_meeting_links_are_not_mistaken_for_addresses():
    url = ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_NjE2ZmY1ZTktYzM0Ny00ZDQ2LWE5MjQt"
           "MGQ3ZTRlYWY0MDU2%40thread.v2/0")
    assert clean_url(url).url == url


def test_content_ids_in_the_query_are_kept():
    drive = "https://drive.google.com/open?id=1a2B3c4D5e6F7g8H9i0JkLmNoPqRsTuV"
    assert clean_url(drive).url == drive


def test_bare_keys_with_tokens_or_addresses_are_dropped():
    assert clean_url("https://example.com/view?bWVAZ21haWwuY29tfDEyMzQ1Ng==").url == "https://example.com/view"
    assert clean_url("https://x.com/c?someone@student.uts.edu.au").url == "https://x.com/c"
    assert clean_url("https://x.com/opts/someone--at--student.uts.edu.au").url is None
    assert clean_url("https://example.com/go?u=me%2540gmail.com").url == "https://example.com/go"


def test_relative_redirect_targets_are_cleaned_too():
    assert clean_url("https://example.com/login?next=%2Fverify%3Fotp%3D482913%26lang%3Dvi").url == (
        "https://example.com/login?next=%2Fverify%3Flang%3Dvi"
    )
    assert clean_url("https://example.com/login?next=%2F%2Fx.com%2Freset%3Ftoken%3DSECRETsecret").url == (
        "https://example.com/login?next=https%3A%2F%2Fx.com%2Freset"
    )


def test_glued_html_tags_in_plain_text_cannot_carry_a_token_past_the_sweep():
    plain = "<p>Apply: https://www.linkedin.com/comm/jobs/view/4012345678/?trk=x&<wbr>otpToken=SECRETONETIME</p>"
    assert "SECRET" not in clean_text(plain)
    assert "SECRET" not in clean_text("Link: https://x.com/a?x=1<br>&token=SECRETsecret")


def test_token_cut_off_by_a_line_wrap_is_swept():
    assert clean_text("Verify: https://x.com/verify?id=1&token=\nAb3kX9qL2mN8pQ4rS7tU1v\nThanks") == (
        "Verify: <https://x.com/verify?id=1>\nThanks"  # rejoined with its name, then dropped
    )


def test_sweep_keeps_ordinary_content():
    for text in ("#AIHackathon2026Round2Winners and #ThôngBáoHọcKỳ1Năm2026Mới", "P&L=revenue-cost, R&D",
                 "Price=5000&qty=2 (from the order form)", "contact: hr@company.vn", "@phuc_nguyen99"):
        assert clean_text(text) == text, text


def test_sweep_runs_in_linear_time_on_huge_words():
    start = time.perf_counter()
    for blob in ("a" * 400_000, "_" * 400_000, "x=" + "a" * 400_000):
        scrub_residue(f"x {blob} y")
    assert time.perf_counter() - start < 2  # the old name regex took minutes
    assert scrub_residue("keep " + "a" * 5_000 + " this") == "keep  this"  # blobs are not content


# --- round-3 review findings ---------------------------------------------------

def test_malformed_links_never_raise():
    for url in ("https://news.example.com/track?redirect=https://[SITE_URL]/welcome",
                "https://example.com/a?next=http%3A%2F%2F%5B", "https://example.com/a?next=https://x／y.com/",
                "https://[::1/a", "http://%zz", "https://" + "a" * 50_000):
        clean_url(url)  # must not raise
    assert html_to_text('<a href="https://news.example.com/track?redirect=https://[SITE_URL]/welcome">Hi</a>') == (
        "Hi <https://news.example.com/track>"
    )


def test_long_urls_are_dropped_fast():
    start = time.perf_counter()
    assert clean_url("https://x.com/" + "a" * 100_000).reason == "too-long"
    clean_text("Link: https://x.com/" + "a@" * 1_000 + " end")
    assert time.perf_counter() - start < 1


def test_empty_value_keys_are_judged_as_keys():
    assert clean_url("https://example.com/view?cGh1Y25ndXllbkBnbWFpbC5jb20=").url == "https://example.com/view"
    assert clean_url("https://example.com/view?Ab3kX9qL2mN8pQ4rS7tU1vW0xYz=").url == "https://example.com/view"
    assert clean_url("https://x.com/a?blank=&token=").url == "https://x.com/a?blank="


def test_sensitive_routes_are_whole_segments_not_words_in_titles():
    for url in ("https://www.notion.so/Lesson-3-Recursion-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
                "https://www.notion.so/Workshop-Session-3-Slides-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
                "https://www.figma.com/design/AbC123xYz789QwErTy456/Login-Screen-Redesign",
                "https://github.com/acme/auth-service/commit/3f786850e387550fdab836ed7e6dc881de23001b"):
        assert clean_url(url).url == url, url
    assert clean_url("https://github.com/password_reset/Ab3kX9qL2mN8pQ4rS7tU1vW0xYz5CdEf").url is None


def test_all_teams_thread_kinds_are_kept():
    for thread in ("thread.tacv2", "thread.skype", "thread.v2"):
        url = f"https://teams.microsoft.com/l/meetup-join/19%3ameeting_NjE2ZmY1ZTktYzM0Ny00ZDQ2%40{thread}/0"
        assert clean_url(url).url == url, thread


def test_nested_targets_in_every_shape_are_cleaned():
    cases = {
        "next=%2Fverification%23code%3D482913": "next=%2Fverification",
        "next=verify%3Fotp%3D482913": "next=%2Fverify",
        "next=https%253A%252F%252Fportal.uni.edu.vn%252Fverify%253Fotp%253D482913":
            "next=https%3A%2F%2Fportal.uni.edu.vn%2Fverify",
        "next=%5C%5Cx.com%2Fverify%3Fotp%3D482913": "next=https%3A%2F%2Fx.com%2Fverify",
    }
    for query, expected in cases.items():
        assert clean_url(f"https://example.com/login?{query}").url == f"https://example.com/login?{expected}", query


def test_substack_reader_id_dropped_on_custom_domains_too():
    assert clean_url("https://www.lennysnewsletter.com/p/how-to-x?r=2bjtip&utm_medium=email").url == (
        "https://www.lennysnewsletter.com/p/how-to-x"
    )
    assert clean_url("https://www.google.com/search?q=hackathon2026").url == "https://www.google.com/search?q=hackathon2026"


def test_hashtag_exemption_is_only_for_real_hashtags():
    assert "SECRET" not in clean_text("Link: <https://x.com/a>#access_token=SECRETsecret&x=1")
    assert "SECRET" not in clean_text("@token=SECRETsecret")


def test_curly_quotes_and_fullwidth_brackets_are_not_part_of_a_url():
    assert clean_text("Đăng ký tại “https://forms.uni.edu.vn/dang-ky” trước thứ Sáu") == (
        "Đăng ký tại “<https://forms.uni.edu.vn/dang-ky>” trước thứ Sáu"
    )
    assert clean_text("（https://example.com/event）") == "（<https://example.com/event>）"


def test_course_code_lists_and_divider_lines_survive():
    for text in ("Prerequisites: COMP1010/COMP2020/INFO3001 and Lab1/Lab2/Lab3/Lab4/Lab5",
                 "Job A\n" + "-" * 40 + "\nJob B", "Reply\n" + "_" * 32 + "\nFrom: Lecturer"):
        assert clean_text(text) == text, text


def test_linkedin_e_v2_wrapper_and_search_filters():
    assert clean_url("https://www.linkedin.com/e/v2?e=3f1-abc&a=customerServiceUrl&url=https%3A%2F%2Fwww.linkedin.com"
                     "%2Fevents%2F7123456789012345678%2F").url == "https://www.linkedin.com/events/7123456789012345678/"
    assert clean_url("https://www.linkedin.com/comm/jobs/search/?f_T=9&f_E=2&f_WT=2&geoId=1&trk=eml").url == (
        "https://www.linkedin.com/comm/jobs/search/?f_T=9&f_E=2&f_WT=2&geoId=1"
    )


# --- final verification findings -----------------------------------------------

def test_wrapped_secret_value_is_dropped_however_it_was_wrapped():
    for text in ("Use https://example.com/verify?otp= 482913 Thanks",          # snippet: wrap became a space
                 "> Use https://example.com/verify?token=\n> AbC123xYz Thanks",  # quoted reply
                 "Use https://example.com/verify#otp=\n482913 Thanks"):          # fragment
        out = clean_text(text)
        assert "482913" not in out and "AbC123xYz" not in out, text
        assert out.endswith("Thanks")
    assert "482913" not in html_to_text("<p>Use https://example.com/verify?otp=\r\n482913</p>")
    assert clean_text("Search https://x.com/find?q= hackathon") == "Search <https://x.com/find?q=> hackathon"


def test_more_fullwidth_brackets_stay_outside_the_url():
    for opening, closing in ("［］", "＜＞", "『』", "《》", "〈〉"):
        assert clean_text(f"{opening}https://example.com/a{closing}") == f"{opening}<https://example.com/a>{closing}"
    assert clean_text("Xem https://example.com/a。") == "Xem <https://example.com/a>。"
