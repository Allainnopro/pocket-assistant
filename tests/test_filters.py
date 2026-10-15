"""Free pre-filter tests: only certain noise goes; anything that might matter stays.

The keep-lists below include every real opportunity or notice that an
earlier, word-based version of the filter wrongly dropped in review.

Run: pytest
"""

import unicodedata

import pytest

from assistant.filters import prefilter
from assistant.profile import Profile


def profile(**overrides):
    fields = dict(interests=(), open_to=(), level="student", home="HCMC", outside_home=-30,
                  remote_ok="vietnam", boost={}, penalize={}, aliases={})
    fields.update(overrides)
    return Profile(**fields)


def verdict(subject, domain="example.com", source="other", labels=(), **profile_overrides):
    return prefilter(subject, domain, source, list(labels), profile(**profile_overrides))


# --- what IS dropped ----------------------------------------------------------------

@pytest.mark.parametrize("subject", [
    "Your verification code is 482913",
    "Mã xác thực của bạn là 482913",
    "482913 is your Facebook code",
    "Your one-time password is 123456",
    "OTP: 77310412",
])
def test_a_subject_that_carries_a_code_is_dropped_from_any_non_academic_sender(subject):
    assert verdict(subject).rule == "one-time-code"
    assert verdict(subject, "linkedin.com", "linkedin").rule == "one-time-code"


@pytest.mark.parametrize("subject, rule", [
    ("Security alert", "security-alert"),
    ("Cảnh báo bảo mật", "security-alert"),                 # the one real drop in the owner's mailbox
    ("Cảnh báo đăng nhập bất thường", "security-alert"),
    ("Microsoft account unusual sign-in activity", "security-alert"),
    ("[GitHub] Please verify your device", "security-alert"),
    ("Microsoft account security code", "one-time-code"),
    ("Mã kích hoạt tài khoản", "one-time-code"),
    ("Reset your password", "password-reset"),
    ("Mật khẩu của bạn đã được thay đổi", "password-reset"),
])
def test_account_notices_are_dropped_only_from_account_providers(subject, rule):
    for provider in ("accounts.google.com", "account.microsoft.com", "github.com"):
        assert verdict(subject, provider).rule == rule, provider
    assert verdict(subject, "uni-portal.example.com").keep  # same words, any other sender: Haiku decides


@pytest.mark.parametrize("subject", ["Thông báo biến động số dư", "MoMo - Giao dịch thành công",
                                     "VCB Digibank - Thông báo giao dịch", "Sao kê tài khoản tháng 9"])
def test_bank_transaction_notices_are_dropped(subject):
    assert verdict(subject, "momo.vn").rule == "bank-notice"


def test_every_spelling_matches():
    nfd = unicodedata.normalize("NFD", "Mã xác thực của bạn là 482913")
    assert verdict(nfd).rule == "one-time-code"
    assert verdict("Ma xac thuc cua ban la 123456").rule == "one-time-code"
    assert verdict(unicodedata.normalize("NFD", "Cảnh báo bảo mật"), "accounts.google.com").rule == "security-alert"


# --- what is never dropped by the built-in rules ----------------------------------

@pytest.mark.parametrize("subject", [
    # Earlier review: opportunities that share words with noise
    "Invoice processing AI challenge - prize 100M VND", "receipt OCR competition: extract totals from photos",
    "Hóa đơn điện tử AI Challenge 2026", "Cuộc thi AI nhận dạng hóa đơn - giải thưởng 200 triệu",
    "Tuyển thực tập sinh xử lý đơn hàng (AI) tại Masan", "MISA tuyển Thực tập sinh lập trình AI - sản phẩm hóa đơn điện tử",
    "Your order of events for Job Fair 2026", "Order #42: Job Fair HCMUT booth ticket",
    "Your receipt: registration for Agentic AI Bootcamp", "Thông báo hóa đơn học phí HK1, hạn nộp 30/10",
    "OTP-based internship test: instructions inside", "Mã xác nhận đăng ký tham gia Hackathon HCMUT 2026",
    "Ngày hội việc làm 2026 - mã xác nhận tham dự của bạn", "OTP Hackathon 2026 by VNPay",
    "Seminar: Password reset flows done right", "Workshop: building a Security alert pipeline with LLMs",
    "CTF 2026: New sign-in challenges released", "Cảnh báo bảo mật AI: hội thảo chuyên đề tại HCMUT",
    "Thông báo: đăng ký nhận mã OTP để đóng học phí qua BIDV trước 30/10",
    "[Devpost] New sign-in flows challenge: Passkeys Hackathon",
    # Verification sweep: reminders, notices and job titles a word-based filter dropped
    "Receipt for your registration: AI Day Vietnam 2026", "Your receipt for Google I/O Extended Ho Chi Minh City",
    "Order #7731042 confirmed: DevFest Saigon 2026 ticket", "Ticketbox - Đơn hàng #582932: Vé tham dự AI Day Vietnam 2026",
    "Payment confirmation – Registration fee for ICPC Regional 2026", "Invoice #2026-10-0012: Tuition fee Semester 1",
    "Payment confirmation: Tuition fee Semester 1 2026-2027", "Order Management System Developer (Java) – FPT Software",
    "Invoice Platform Backend Developer – MISA", "Invoice Specialist (Fresher) – MISA",
    "Order Fulfillment Associate – Shopee HCMC", "Receipt Scanning ML Engineer – Momo (Junior)",
    "Thông báo: cổng đăng nhập mới cho sinh viên từ 01/11",
    "Security alert: phishing emails targeting student accounts – please read",
    "Cảnh báo bảo mật: email giả mạo phòng Đào tạo",
    "Mời tham dự buổi chia sẻ 'Security alert fatigue and LLMs' – CLB An toàn thông tin",
    "Verify your email to complete your application at Bosch",
    "Please verify your email address to continue your application – Grab Careers",
    "Xác nhận thanh toán lệ phí đăng ký thi TOEIC đầu ra",
    "Hướng dẫn đổi mật khẩu tài khoản sinh viên trên cổng myBK",
    "Sinh viên bắt buộc đổi mật khẩu email trường trước 30/10", "Thong bao doi mat khau he thong BKeL truoc ngay 30/10",
    "Thông báo về việc thay đổi mật khẩu Wi-Fi sinh viên", "Reset your password for the new LMS portal before 1 Nov",
    "Mã kích hoạt tài khoản thư viện số cho sinh viên",
    "Coding test instructions: log in with the one-time password we sent you",
    "Your one-time password to access the FPT online assessment", "Interview booked: your confirmation code is 7731",
])
def test_notices_reminders_and_opportunities_are_kept(subject):
    for domain, source in (("example.com", "other"), ("hcmut.edu.vn", "other"),
                           ("uts.edu.au", "university"), ("linkedin.com", "linkedin")):
        assert verdict(subject, domain, source).keep, (subject, source)


def test_academic_senders_are_never_dropped_by_built_in_rules():
    assert verdict("Your verification code is 482913", "it.hcmut.edu.vn").keep
    assert verdict("Security alert", "student.uts.edu.au").keep


def test_promotions_tab_is_kept_unless_the_owner_opts_in():
    jam = ("Build Your Game Challenge: Join in and Compete", "jetbrains.com", "other", ["CATEGORY_PROMOTIONS"])
    assert prefilter(*jam, profile()).keep
    assert prefilter(*jam, profile(drop_promotions=True)).keep  # an opportunity, even in Promotions
    ad = ("Flash sale: 30% off all courses", "shop.example.com", "other", ["CATEGORY_PROMOTIONS"])
    assert prefilter(*ad, profile()).keep
    assert prefilter(*ad, profile(drop_promotions=True)).rule == "promotions"


# --- the owner's own rules ------------------------------------------------------------

def test_owner_rules_apply_to_every_source():
    assert verdict("New message", "discord.com", drop_sender_domains=("discord.com",)).rule == "sender:discord.com"
    assert verdict("Hi", "click.discord.com", drop_sender_domains=("discord.com",)).rule == "sender:discord.com"
    assert verdict("Hi", "notdiscord.com", drop_sender_domains=("discord.com",)).keep  # suffix, not substring
    assert verdict("Your order #123", "shopee.vn", drop_sender_domains=("shopee.vn",)).rule == "sender:shopee.vn"
    assert verdict("Weekly AI digest #42", drop_subject_patterns=("weekly ai digest",)).rule == "subject-pattern:1"
    nfd = unicodedata.normalize("NFD", "Thông báo tự động từ hệ thống")
    assert verdict(nfd, drop_subject_patterns=("thông báo tự động",)).rule == "subject-pattern:1"


def test_the_rule_never_carries_mail_text():
    assert verdict("Your verification code is 482913 for phuc@example.com").rule == "one-time-code"


def test_a_year_is_not_a_code():
    assert verdict("Room code: 2026 hall B").keep
    assert verdict("Your code is 482913").rule == "one-time-code"
    assert verdict("Ma OTP cua quy khach la 654321").rule == "one-time-code"  # three words before "là"
