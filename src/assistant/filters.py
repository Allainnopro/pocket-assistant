"""Free pre-filter: drop only what is CERTAINLY noise, before any paid model call.

Pure (no network, no database), like parser.py. Why so cautious? A wrong
drop deletes an email for good; a wrong keep costs about $0.0004 of Haiku
time, and Haiku judges content far better than subject-line rules. A
150-subject review showed that subject words alone can't tell noise from
action items: "Order #77: DevFest ticket", "Invoice Platform Developer -
MISA", "Sinh viên bắt buộc đổi mật khẩu trước 30/10" all look like noise
and aren't. So the built-in rules are:

1. one-time codes, password resets and security alerts, but only from the
   account providers themselves (Google, Microsoft, GitHub, Apple): the
   sender is far more reliable than the wording;
2. from anyone else, only an email that actually carries a code ("Your
   verification code is 482913"), so a live code isn't sent to the API;
3. bank transaction notices ("biến động số dư"), which carry balances.

University mail, any .edu/.ac sender, and any subject with opportunity
words (hackathon, hội thảo, interview, ứng tuyển...) are never dropped by
these rules. The owner's own rules in profile.toml apply on top, to every
source (that is where shop senders go). All matching runs on folded text
(textnorm.fold), so accents, case and NFD/NFC forms don't matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .profile import Profile
from .textnorm import fold


@dataclass(frozen=True)
class Verdict:
    keep: bool
    rule: str | None = None  # why it was dropped: a rule name, never mail text


ACCOUNT_PROVIDERS = (
    "accounts.google.com", "account.microsoft.com", "accountprotection.microsoft.com",
    "github.com", "appleid.apple.com", "id.apple.com",
)
ACADEMIC = re.compile(r"(^|\.)(edu|ac)(\.[a-z]{2,3})?$")  # uts.edu.au, hcmut.edu.vn, x.ac.uk, mit.edu

# All patterns match FOLDED text: lower case, no accents.
OPPORTUNITY_WORDS = re.compile(
    r"\b(hackathon|ideathon|datathon|challenge|competition|contest|cuoc thi|olympic|ctf|giai thuong|prize"
    r"|award|tuyen|thuc tap|intern\w*|jobs?|viec lam|career|recruit\w*|ngay hoi|job fair|hoi thao|seminar"
    r"|workshop|webinar|bootcamp|summit|conference|meetup|hoc phi|tuition|hoc bong|scholarship|exam"
    r"|interview|phong van|assessment|coding test|bai test|application|ung tuyen|offer)\b"
)

ACCOUNT_RULES = (  # only for mail from ACCOUNT_PROVIDERS
    ("one-time-code", re.compile(
        r"(verification|security|login|sign[- ]?in|one[- ]time|single[- ]use|2fa|otp) (code|password|passcode)"
        r"|one[- ]time pass(word|code)|\bma (otp|xac (thuc|minh|nhan)|kich hoat)\b")),
    ("password-reset", re.compile(
        r"reset (your )?password|password reset|password (was |has been )?changed"
        r"|(dat lai|khoi phuc|doi|thay doi) mat khau|mat khau .{0,30}(thay doi|doi)\b")),
    ("security-alert", re.compile(
        r"security alert|new sign-?in|sign-?in attempt|unusual sign-?in|login alert|verify your device"
        r"|canh bao bao mat|dang nhap (moi|bat thuong|tu thiet bi moi)")),
)

# From anyone: the subject itself delivers a code.
_CODE = r"(?!(?:19|20)\d{2}\b)\d{4,8}\b"  # 4-8 digits, but not a year like 2026
CODE_DELIVERY = re.compile(  # "code is 482913", "mã xác thực của bạn là 482913", "482913 is your code"
    rf"\b(code|otp|passcode|password|ma (otp|xac thuc|xac minh))\b(?:\s+\w+){{0,3}}\s*(?:is|la|:)\s*{_CODE}"
    rf"|\b{_CODE}\W{{0,3}}(is your|la ma)\b"
)
BANK_NOTICE = re.compile(r"bien dong so du|sao ke tai khoan|giao dich thanh cong|thong bao giao dich")


def _domain_match(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith("." + rule)


def prefilter(subject: str, sender_domain: str, source: str, labels: list[str], profile: Profile) -> Verdict:
    """Decide, for free, whether an email is worth a model call."""
    folded = fold(subject)
    domain = sender_domain.lower()

    # The owner's own rules first: they are deliberate, and apply to every source.
    for rule_domain in profile.drop_sender_domains:
        if _domain_match(domain, rule_domain):
            return Verdict(False, f"sender:{rule_domain}")
    for i, phrase in enumerate(profile.drop_subject_patterns):
        if fold(phrase) in folded:
            return Verdict(False, f"subject-pattern:{i + 1}")

    if source == "university" or ACADEMIC.search(domain) or OPPORTUNITY_WORDS.search(folded):
        return Verdict(True)  # notices and opportunities are the whole point

    if any(_domain_match(domain, provider) for provider in ACCOUNT_PROVIDERS):
        for name, pattern in ACCOUNT_RULES:
            if pattern.search(folded):
                return Verdict(False, name)
    if CODE_DELIVERY.search(folded):
        return Verdict(False, "one-time-code")
    if BANK_NOTICE.search(folded):
        return Verdict(False, "bank-notice")
    if source == "other" and profile.drop_promotions and "CATEGORY_PROMOTIONS" in labels:
        return Verdict(False, "promotions")
    return Verdict(True)
