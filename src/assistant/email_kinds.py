"""Each email's kind, by rules: sure only for known senders and unmistakable words.

Pure (no network, no database), like filters.py. When the rules are sure, they
decide. When they aren't, the AI that already reads the email decides (the
extraction call names the kind), and if it never does, the rules' guess stands.
That guess is never a grey kind: a useful email hidden in "Other" is worse than
a concert in "Important". All matching runs on folded text (textnorm.fold).

The tables below hold only platforms anyone's mail shows. The owner's own senders
(their university, program office, newsletters) are personal: they live in
config.toml [kinds] (KindSenders), which `configure` loads at start, and never in
this public code. Bump RULES_VERSION whenever a rule here changes; together with
the owner's senders it makes rules_stamp, and a new stamp re-sorts stored mail.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email.utils import parseaddr
from functools import cache

from .filters import ACADEMIC, ACCOUNT_PROVIDERS
from .parser import normalize_address
from .textnorm import fold

KINDS = ("study", "uni_admin", "hackathon", "jobs", "scholarship", "news_events",
         "uni_mass", "social", "promo", "accounts", "unsorted")
GREY = frozenset({"uni_mass", "social", "promo", "accounts"})  # these leave Important
RULES_VERSION = 2


@dataclass(frozen=True)
class RuleKind:
    kind: str
    sure: bool


@dataclass(frozen=True)
class KindSenders:
    """The owner's own senders, from config.toml [kinds]: domains match themselves and their subdomains;
    words match whole words, without accents or case."""
    study_domains: tuple[str, ...] = ()  # their university's courses and staff
    study_sender_words: tuple[str, ...] = ()  # in a sender's name, e.g. their degree program's office
    hackathon_domains: tuple[str, ...] = ()
    hackathon_words: tuple[str, ...] = ()  # in subjects: hackathons they follow by name
    promo_domains: tuple[str, ...] = ()  # newsletters and tool vendors (a contest or scholarship still shows)
    accounts_domains: tuple[str, ...] = ()  # services' own account and sign-in mail


_owner = KindSenders()


def configure(senders: KindSenders) -> None:
    """Load the owner's senders (config.toml [kinds]); the CLI does this once, after reading the config."""
    global _owner
    _owner = senders


def rules_stamp() -> str:
    """What the stored rule answers were made with: the rules' version and the owner's senders."""
    return f"{RULES_VERSION}:{hashlib.sha256(repr(_owner).encode()).hexdigest()[:12]}"


def _words(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b")


@cache
def _any_word(words: tuple[str, ...]) -> re.Pattern[str] | None:
    return _words("|".join(re.escape(fold(w)) for w in words)) if words else None


STUDY_DOMAINS = ("edstem.org", "instructure.com")  # Ed and Canvas
SOCIAL_DOMAINS = ("facebookmail.com", "discord.com")
HACKATHON_DOMAINS = ("devpost.com", "kaggle.com")

HACKATHON = _words("hackathon|ideathon|datathon|competition launch")
CONTEST = _words("cuoc thi|competition|contest|challenge|giai thuong|award|olympic|win a trip|compete for")
SCHOLARSHIP = _words("hoc bong|scholarships?")
UNI_ADMIN = _words("tram y te|kham suc khoe|bao hiem y te|bhyt|khao sat su hai long")
JOBS = _words("tuyen dung|viec lam|ngay hoi viec lam|job fair|career seminar|co hoi nghe nghiep|recruit\\w*"
              "|is hiring|hiring|hired near you|job alerts?|you may be a fit|thuc tap sinh|internships?"
              "|interview\\w*|phong van|ung tuyen|job offer|offer letter")
LINKEDIN_POST = _words("recently posted|shared a post|post and your next steps|share their thoughts")
ACCOUNT_WORDS = _words("security alert|canh bao bao mat|xac minh|verify|verification|one-time|ma xac thuc"
                       "|login to|dang nhap|confirm your email|chia se mot so du lieu|xac nhan chuyen tiep"
                       "|pin|new device|2-step|xac minh 2 buoc|privacy policy|che do cai dat|thiet lap")
ONBOARDING = _words("welcome|getting started|new achievement|officially a kaggler|free trial|courses?")
ED_SIGN_IN = _words("login to ed|sign in to ed|sign-in code|verification code")  # not "please verify your marks"
# Folded, a lower-case "khan" is also khó khăn (hardship), khán giả (audience) and the name Khan: only KHẨN in
# capitals, or with a word that makes it "urgent", counts.
URGENT = _words("khan cap|khan truong|thong bao khan|bat buoc|action required|(?<!non-)(?<!non )urgent")
URGENT_CAPS = re.compile(r"\bKHAN\b")


def _from(domain: str, rules: tuple[str, ...]) -> bool:
    return any(domain == rule or domain.endswith("." + rule) for rule in rules)


def _has(pattern: re.Pattern[str] | None, text: str) -> bool:
    return bool(pattern and pattern.search(text))


def sort_by_rules(sender: str, sender_domain: str, subject: str, source: str,
                  my_addresses: tuple[str, ...]) -> RuleKind:
    """The kind the rules give, and whether they are sure of it."""
    name, address = parseaddr(sender)
    who, text, domain = fold(name), fold(subject), sender_domain.lower()
    university = source == "university" or bool(ACADEMIC.search(domain))
    owner = _owner
    hackathon = HACKATHON.search(text) or _has(_any_word(owner.hackathon_words), text)

    if address and normalize_address(address) in {normalize_address(a) for a in my_addresses}:
        return RuleKind("accounts", True)  # the owner's own test and forward mail
    if _from(domain, STUDY_DOMAINS + owner.study_domains) or _has(_any_word(owner.study_sender_words), who):
        if _from(domain, ("edstem.org",)) and ED_SIGN_IN.search(text):
            return RuleKind("accounts", True)  # "Login to Ed": a sign-in code, not the course
        return RuleKind("study", True)
    if _from(domain, ACCOUNT_PROVIDERS + owner.accounts_domains):
        return RuleKind("accounts", True)
    if _from(domain, SOCIAL_DOMAINS):
        return RuleKind("social", True)
    if _from(domain, ("linkedin.com",)):
        if who.startswith("linkedin via"):
            return RuleKind("promo", True)  # LinkedIn's own editorial digests, not a person writing "via LinkedIn"
        if "(via linkedin)" in who:  # a person's message, maybe a recruiter's interview invitation: the AI decides
            return RuleKind("jobs", False) if JOBS.search(text) else RuleKind("unsorted", False)
        if LINKEDIN_POST.search(text):
            return RuleKind("social", True)  # a recruiter's feed post, even about hiring
        if "job alerts" in who or JOBS.search(text):
            return RuleKind("jobs", True)
        if ACCOUNT_WORDS.search(text):
            return RuleKind("accounts", True)
        return RuleKind("social", True)
    if _from(domain, HACKATHON_DOMAINS + owner.hackathon_domains) or "kaggle" in who:
        if hackathon:  # "Welcome to <a hackathon>… next steps" is the hackathon, not onboarding
            return RuleKind("hackathon", True)
        if ONBOARDING.search(text):
            return RuleKind("promo", True)
        if ACCOUNT_WORDS.search(text):
            return RuleKind("accounts", True)
        return RuleKind("hackathon", True)
    if _from(domain, owner.promo_domains + ("google.com",)):
        if ACCOUNT_WORDS.search(text):
            return RuleKind("accounts", True)
        if hackathon:
            return RuleKind("hackathon", True)
        if CONTEST.search(text):
            return RuleKind("hackathon", False)  # a vendor's contest: the AI decides
        if SCHOLARSHIP.search(text):
            return RuleKind("scholarship", False)
        if _from(domain, owner.promo_domains) or who.startswith("google"):
            return RuleKind("promo", True)

    if hackathon:
        return RuleKind("hackathon", True)
    if university:
        if SCHOLARSHIP.search(text):
            return RuleKind("scholarship", True)
        if UNI_ADMIN.search(text):  # by subject: the health office also sends talks, which the AI sorts
            return RuleKind("uni_admin", True)
        if JOBS.search(text):
            return RuleKind("jobs", True)
        if CONTEST.search(text):
            return RuleKind("hackathon", False)  # contests outside the owner's field are mass mail
        return RuleKind("news_events", False)
    if JOBS.search(text):
        return RuleKind("jobs", False)
    if CONTEST.search(text):
        return RuleKind("hackathon", False)
    return RuleKind("unsorted", False)


def urgent_by_rules(subject: str) -> bool:
    """Marked urgent or mandatory in the subject (KHẨN, BẮT BUỘC, Action required)."""
    return bool(URGENT.search(fold(subject)) or URGENT_CAPS.search(fold(subject, keep_case=True)))
