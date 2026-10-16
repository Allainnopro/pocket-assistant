"""Dedupe keys and profile scoring for opportunities. Pure: no database, no network.

Why pure? `assistant rescore` re-ranks everything after you edit profile.toml,
for free, by running these functions over stored fields. No model call is
needed to change your mind about what matters.

Scoring, in order:
- start from the model's relevance (0-100);
- add each [boost]/[penalize] keyword found in the title, organizer or
  location, once per keyword. Not in the eligibility text or the model's
  "why" line: words like "Middle" or "Remote" are harmless in a job title
  but over-match in prose ("middle of the week", "remote sensing");
- apply the location rule from location_scope, not from words: keywords
  can't tell "HCMC office" from "Hanoi office, HCMC candidates welcome".
  Your home keyword (e.g. HCMC = 20) counts when the item IS at home (or,
  when the model couldn't tell, when the location text names it);
  outside_home (e.g. -30) when it is elsewhere, abroad, or remote in a way
  remote_ok doesn't allow. A boost found only in the location of such a
  place doesn't count: "On-site in Hanoi" earns no On-site bonus;
- a kind you're not open to: -50.
The total is NOT capped at 100: a cap would erase exactly these penalties
among the best items (an AI internship in Hanoi and the same one in HCMC
would both hit 100), so the ranking uses the full sum.

Known limit: a keyword in capitals ("AI") must appear in capitals, which
tells the acronym from the Vietnamese word "ai" ("who") in normal text, but
not in a title written ALL IN CAPITALS ("AI CŨNG CÓ THỂ..."). Rare; the
model's relevance usually keeps such items low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, urlencode, urlsplit

from .profile import Profile
from .textnorm import fold

NOT_OPEN_TO = -50
NO_DEADLINE = "nodate"


def _forms(*forms: str) -> list[tuple[str, ...]]:
    return sorted((tuple(f.split()) for f in forms), key=len, reverse=True)  # longest first


# Company legal forms, folded. Stripped from the front and the end of an
# organizer, so "Công ty TNHH ABC" and "ABC Co., Ltd" both become "abc".
# Separate lists: "Co." is a legal form only at the end ("Co.op Mart" keeps it).
LEGAL_PREFIXES = _forms("tong cong ty", "cong ty", "cty", "tap doan", "trach nhiem huu han", "tnhh",
                        "mot thanh vien", "mtv", "co phan", "cp")
LEGAL_SUFFIXES = _forms("co ltd", "co", "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
                        "company", "jsc", "plc", "gmbh", "pte", "tnhh", "mtv", "cp", "group",
                        "joint stock company", "joint stock", "limited liability company", "limited liability",
                        "one member limited liability company", "one member")
LEGAL_WORDS = {w for form in LEGAL_PREFIXES + LEGAL_SUFFIXES for w in form}
# Names that punctuation-stripping would merge: C++, C# and C are different jobs
TECH_NAMES = [(re.compile(r"c\+\+"), " cpp "), (re.compile(r"c#"), " csharp "), (re.compile(r"f#"), " fsharp "),
              (re.compile(r"\.net\b"), " dotnet ")]
TRACKING_PARAMS = re.compile(r"utm_\w+|fbclid|gclid|mc_cid|mc_eid|trk|trackingid|refid")
_NON_WORD = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class Opportunity:
    """The fields an opportunity is ranked and deduped on (from extraction, or a stored row)."""
    kind: str
    title: str
    organizer: str | None
    deadline: str | None        # YYYY-MM-DD
    location: str | None
    location_scope: str
    eligibility: str | None
    prize_or_pay: str | None
    url: str | None
    relevance: int
    why: str


def words(text: str | None, keep_case: bool = False) -> list[str]:
    """Accent-folded words, punctuation dropped: 'TP.HCM - Thủ Đức' -> ['tp', 'hcm', 'thu', 'duc']."""
    return _NON_WORD.sub(" ", fold(text or "", keep_case)).split()


def title_key(title: str, organizer: str | None = None) -> str:
    """Word order doesn't matter; numbers (the year) do. "C++", "C#" and "C" stay
    apart. The organizer's name inside the title is dropped, so "Software Engineer
    Intern - FPT Software" matches "Software Engineer Intern" at FPT Software."""
    text = fold(title)
    for pattern, name in TECH_NAMES:
        text = pattern.sub(name, text)
    found = words(text)
    org = org_key(organizer).split()
    if org and len(found) > len(org):
        for i in range(len(found) - len(org) + 1):
            if found[i:i + len(org)] == org:
                found = found[:i] + found[i + len(org):]
                break
    return " ".join(sorted(found))


def org_key(organizer: str | None) -> str:
    found = words(organizer)
    stripped = True
    while stripped:  # "Công ty TNHH MTV ABC" takes three passes
        stripped = False
        for form in LEGAL_PREFIXES:
            if len(found) > len(form) and tuple(found[:len(form)]) == form:
                found, stripped = found[len(form):], True
        for form in LEGAL_SUFFIXES:
            if len(found) > len(form) and tuple(found[-len(form):]) == form:
                found, stripped = found[:-len(form)], True
    if all(w in LEGAL_WORDS for w in found):
        return ""  # "Công ty TNHH" names nobody: let the link or the email stand in
    return " ".join(found)


def url_key(url: str | None) -> str:
    """Host, path and query (sorted, tracking parameters dropped), without the
    scheme, "www." or a trailing slash. The query stays: ?jobId=1 and ?jobId=2
    are different jobs."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not TRACKING_PARAMS.fullmatch(k.lower()))
    key = f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"
    return f"{key}?{urlencode(query)}" if query else key


def keys(opp: Opportunity, message_id: str) -> tuple[str, str]:
    """(base_key, dedupe_key). Without an organizer the link stands in, then the
    email itself: two "Tuyển thực tập sinh AI" posts from unnamed companies
    must not merge."""
    who = org_key(opp.organizer) or url_key(opp.url) or f"msg:{message_id}"
    base = f"{title_key(opp.title, opp.organizer)}|{who}"
    return base, f"{base}|{opp.deadline or NO_DEADLINE}"


def merge(old: Opportunity, new: Opportunity) -> Opportunity:
    """A second sighting of the same opportunity. It only fills what was missing,
    and keeps the higher relevance with the "why" and kind that go with it."""
    def fill(a, b):
        return a if a else b
    better = new if new.relevance > old.relevance else old
    return replace(
        old,
        organizer=fill(old.organizer, new.organizer), deadline=fill(old.deadline, new.deadline),
        location=fill(old.location, new.location), eligibility=fill(old.eligibility, new.eligibility),
        prize_or_pay=fill(old.prize_or_pay, new.prize_or_pay), url=fill(old.url, new.url),
        location_scope=new.location_scope if old.location_scope == "unknown" else old.location_scope,
        relevance=better.relevance, why=better.why, kind=better.kind,
    )


def _found(spelling: str, text_words: list[str], text_cased: list[str]) -> bool:
    """Whole words, accents ignored. A spelling in capitals ("AI", "FPT") must
    appear in capitals: the Vietnamese word "ai" ("who") is not AI. Other
    spellings also match a plural ("Internship" finds "Summer Internships")."""
    caps = spelling.isupper() and sum(c.isalpha() for c in spelling) >= 2
    wanted = words(spelling, keep_case=caps)
    haystack = text_cased if caps else text_words
    n = len(wanted)
    if not n:
        return False
    last = {wanted[-1]} if caps else {wanted[-1], wanted[-1] + "s", wanted[-1] + "es"}
    return any(haystack[i:i + n - 1] == wanted[:-1] and haystack[i + n - 1] in last
               for i in range(len(haystack) - n + 1))


Text = tuple[list[str], list[str]]  # (folded words, folded words keeping capitals)


def _text(text: str | None) -> Text:
    return words(text), words(text, keep_case=True)


def _mentions(keyword: str, profile: Profile, text: Text) -> bool:
    return any(_found(s, *text) for s in profile.spellings(keyword))


def location_weight(opp: Opportunity, profile: Profile) -> tuple[str, int] | None:
    """The location rule: (reason, weight), or None when location changes nothing."""
    home_bonus = profile.boost.get(profile.home, 0)
    scope = opp.location_scope
    if scope == "hcmc":
        return (f"at home ({profile.home})", home_bonus) if home_bonus else None
    if scope == "unknown":
        # The model couldn't tell; the location text may still say so ("TP.HCM")
        if home_bonus and profile.home and _mentions(profile.home, profile, _text(opp.location)):
            return f"at home ({profile.home}, from the location text)", home_bonus
        return None
    allowed_remote = {"anywhere": ("remote_vietnam", "remote_international"),
                      "vietnam": ("remote_vietnam",), "no": ()}[profile.remote_ok]
    if scope in allowed_remote:
        return None
    reason = {"vietnam_other": "elsewhere in Vietnam", "abroad": "abroad",
              "remote_vietnam": "remote", "remote_international": "remote outside Vietnam"}[scope]
    return (reason, profile.outside_home) if profile.outside_home else None


def score(opp: Opportunity, profile: Profile) -> tuple[int, list[tuple[str, int]]]:
    """(score, breakdown). The score is the full sum, not capped (see the module
    docstring). The breakdown shows why, for tuning profile.toml."""
    parts: list[tuple[str, int]] = [("relevance", opp.relevance)]
    located = location_weight(opp, profile)
    unwanted_place = located is not None and located[1] < 0
    named = _text(" ".join(t for t in (opp.title, opp.organizer) if t))
    place = _text(opp.location)
    for keyword, weight in {**profile.boost, **profile.penalize}.items():
        if keyword == profile.home:
            continue  # counted by the location rule instead
        if _mentions(keyword, profile, named):
            parts.append((keyword, weight))
        elif _mentions(keyword, profile, place) and not (weight > 0 and unwanted_place):
            parts.append((keyword, weight))  # "On-site in Hanoi" earns no On-site bonus
    if located:
        parts.append(located)
    if opp.kind not in profile.open_to:
        parts.append((f"not open to {opp.kind}", NOT_OPEN_TO))
    return sum(w for _, w in parts), parts


def explain(parts: list[tuple[str, int]]) -> str:
    """[('relevance', 85), ('AI', 20), ('elsewhere in Vietnam', -30)] -> 'relevance 85, +20 AI, -30 elsewhere in Vietnam'."""
    base, *rest = parts
    return ", ".join([f"relevance {base[1]}", *(f"{w:+d} {reason}" for reason, w in rest)])
