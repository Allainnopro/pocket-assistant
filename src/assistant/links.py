"""Links in email bodies: keep the real destination, drop the tracking.

Pure functions, like parser.py: no network, never follows a link (following
a tracker link would "click" it).

Why bother? Phase 0 threw HTML links away, so alert emails reached the brain
with no URLs at all. Keeping them raw is worse: LinkedIn job links carry a
one-time login token (otpToken), and newsletter trackers embed IDs that tie a
click to this inbox. Those must never reach Claude or Telegram.

Three layers, because no single rule catches everything:
1. clean_url judges each URL: tracker hosts and token-shaped path segments
   (and fragments) are dropped; each parameter is judged by its NAME (known
   tracking/secret names) and its VALUE (an email address, a JWT, a random
   token, or a nested URL, which is cleaned recursively, so redirect
   wrappers are unwrapped). Kept parameters stay byte-identical.
2. clean_text_urls applies that to every URL in the finished body text, in
   one pass, so pieces split by HTML tags rejoin first.
3. scrub_residue removes leftovers outside our cleaned links: query tails cut
   off by a space or a line break, "token=..." on its own, random tokens.

Known limits, accepted on purpose: voucher codes and commit hashes in prose
look like tokens and are removed; a short secret (under 6 characters) under
a neutral parameter name can survive.

The name lists come from a survey of the owner's real mail (hosts and
parameter names only); `assistant links` reruns that survey.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, unquote_plus, urljoin, urlsplit, urlunsplit

MAX_URL_CHARS = 500     # longest cleaned URL we keep
MAX_INPUT_CHARS = 4000  # longer raw URLs are dropped unread (no real link is that long)
MAX_NESTING = 3
MAX_WORD_CHARS = 2000  # a "word" this long in prose is a blob, never content

# Sites whose real destination is the path; every parameter is tracking except
# a small allowlist of ones that change what the page shows (a job search).
PATH_ONLY_DOMAINS = ("linkedin.com", "linkedinmobileapp.com", "facebook.com")
PATH_ONLY_KEEP = {"keywords", "geoid", "f_tpr", "currentjobid", "f_t", "f_e", "f_wt", "f_jt", "f_c",
                  "location", "distance"}

# Real content IDs look random (Google Forms, Drive, Notion, Teams meetings):
# not "random-path" there, unless the page is a reset/verify/invite page.
CONTENT_ID_DOMAINS = ("docs.google.com", "drive.google.com", "notion.so", "notion.site",
                      "youtube.com", "youtu.be", "figma.com", "github.com", "teams.microsoft.com")
CONTENT_ID_PARAMS = {"id", "list", "p", "v"}
# Job boards use UUIDs as job IDs; everywhere else a UUID in a path is treated as a token.
JOB_BOARD_DOMAINS = ("lever.co", "greenhouse.io", "ashbyhq.com", "workable.com", "smartrecruiters.com",
                     "myworkdayjobs.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "breezy.hr")

# Wrappers whose real destination sits in a parameter: (domain, path must contain, params)
REDIRECTORS = (
    ("google.com", "/url", ("q", "url")),
    ("safelinks.protection.outlook.com", "", ("url",)),
    ("accounts.google.com", "", ("continue",)),
    ("linkedin.com", "/redir", ("url",)),
    ("linkedin.com", "/e/v2", ("url",)),
)

# Click-tracking redirectors that hide the destination: nothing worth keeping.
TRACKER_DOMAINS = ("c.gle", "click.discord.com", "r.openai.com", "sendgrid.net", "list-manage.com",
                   "mailgun.org", "mandrillapp.com", "hubspotlinks.com", "exct.net", "klclick.com",
                   "links.iterable.com", "mlsend.com")
TRACKER_HOST_PREFIXES = ("click.", "clicks.", "track.", "trk.", "elink")
TRACKER_PARAMS = {"upn"}  # SendGrid-style click payload on a branded host

# Reader IDs that only mean something on one service.
HOST_DROP_PARAMS = {
    "substack.com": {"r"},
    "mailchi.mp": {"e", "u"},
    "campaign-archive.com": {"e", "u"},
    "forward-to-friend.com": {"e", "u"},
}

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "yclid", "ttclid", "twclid", "gbraid", "wbraid",
    "li_fat_id", "_branch_match_id", "lipi", "eid", "refid", "trackingid", "aref", "ref", "ref_src",
    "referrer", "si", "spm", "mid", "bcode", "medium", "n_m", "n_sg", "rms", "irms", "lfes", "lloc",
    "origin", "_kx", "vgo_ee", "et_rid", "sdata", "reserved", "usg", "sa", "ved",
}
TRACKING_PREFIXES = ("utm_", "mc_", "mkt_", "_hs", "__hs", "hs_", "trk", "pk_", "oly_", "vero_",
                     "ml_subscriber", "ck_subscriber")
SECRET_PARAM = re.compile(
    r"token|otp|signature|sig$|^_sig|auth|session|login|passw|^pwd$|^pin$|e?mail|user_?id|^uid$"
    r"|apikey|^key$|hint|^code$|oobcode|^sid$|jwt|ticket|secret|nonce|invite|subscriber|recipient|^to$",
    re.IGNORECASE,
)

# Footer links: matched on the WHOLE link text, or on the URL path, never on
# a substring of a real title ("Privacy & AI Hackathon" stays).
FOOTER_TEXT = re.compile(
    r"\W*(unsubscribe( here| from (this|these) emails?)?|opt[ -]?out"
    r"|(manage|update|change|edit) (your )?(email |notification |subscription )?"
    r"(preferences|settings|subscriptions?)|(email|notification) (preferences|settings)"
    r"|(view|open|read)( this| the)?( email| it)?( (in|on) (your |a )?browser| online| as a web ?page)"
    r"|web version|online version|privacy( policy| notice| statement)?)\W*",
    re.IGNORECASE,
)
FOOTER_PATH = re.compile(
    r"/(email-)?(unsubscribe|optout|opt-out|preferences|email-preferences|privacy(-policy)?)(/|$)",
    re.IGNORECASE,
)

# group 1: one optional leading space, so a dropped link doesn't leave a gap behind
URL_IN_TEXT = re.compile(
    r"( ?)<?((?:https?://|(?<![\w@./-])www\.)[^\s<>\"“”‘’«»（）【】「」［］＜＞『』《》〈〉。]+)>?", re.IGNORECASE
)
OUR_LINK = re.compile(r"<https?://[^\s<>]*>")  # a link already cleaned by clean_text_urls
HASHTAG = re.compile(r"[#@][^\W_][\w.]*")  # "#AIHackathon2026", "@phuc_nguyen99" (no '=' '&' '%')
PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,40}")  # an ordinary parameter name
ASSIGNMENT = re.compile(r"(?<![\w.%-])([A-Za-z_][\w.%-]{0,40})=")  # anchored: linear time
# Left-anchored so a long word is scanned once, not retried from every position
ADDRESS = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# A path segment that IS one of these routes marks a reset/verify/invite page
# (whole segment, so "Lesson" or "Login-Screen-Redesign" titles don't count).
SENSITIVE_ROUTE = re.compile(
    r"(reset|forgot|set|new|change)?[-_]?password([-_]?(reset|new))?|reset|verify|verification|confirm"
    r"(_verification|[-_]email)?|invite|invites|team_invite|redeem|magic([-_]link)?|login|signin|sign-in"
    r"|auth|activate|activation|recover|recovery|unsubscribe|otp|sso|claim|approve|validate|device",
    re.IGNORECASE,
)
TEAMS_THREAD = re.compile(r"19:[^@/]+@(thread\.(v2|tacv2|skype)|unq\.gbl\.spaces)", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    url: str | None   # cleaned URL, or None if it should not be kept
    reason: str       # "kept", or why it was dropped


def _domain_match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _decode(text: str) -> str:
    """Undo percent-encoding up to twice (double-encoded values hide '@' too)
    and the common "--at--" / "(at)" address obfuscations."""
    once = unquote_plus(text)
    twice = unquote_plus(once) if "%" in once else once
    return re.sub(r"--at--|\(at\)|\[at\]", "@", twice, flags=re.IGNORECASE)


def _has_address(text: str) -> bool:
    return bool(ADDRESS.search(_decode(text)))


# --- random-token detection --------------------------------------------------

def _switches(run: str, digits: bool) -> int:
    """How often a run flips between letters and digits, or between cases."""
    if digits:
        return sum(1 for a, b in zip(run, run[1:]) if a.isalnum() and b.isalnum() and a.isdigit() != b.isdigit())
    return sum(1 for a, b in zip(run, run[1:]) if a.isalpha() and b.isalpha() and a.islower() != b.islower())


def _tokenish(run: str) -> bool:
    # Digits interleaved with letters ("a7Kq2"), not a word with a year ("Hackathon2026")
    if _switches(run, digits=True) >= 2:
        return True
    # A case jumble ("qXbRtZm"), not CamelCase words: real words leave long lowercase stretches
    letters = sum(c.isalpha() for c in run)
    lower_runs = [len(x) for x in re.findall(r"[a-z]+", run)]
    return (letters >= 6 and _switches(run, digits=False) >= max(3, letters // 3)
            and bool(lower_runs) and sum(lower_runs) / len(lower_runs) < 3)


def looks_random(text: str) -> bool:
    """A token or hash rather than a word, slug or plain number.

    Percent-encoding is undone first ("Th%C3%B4ng%20b%C3%A1o" is a title, not
    noise). '-' and '_' are part of the base64url alphabet, so they can't be
    trusted as word separators: the string is split on them and each run is
    judged. Measured on 13,000 random tokens and 9,000 realistic slugs: misses
    0.24% of tokens, wrongly flags 0.04% of slugs.
    """
    text = unquote(text)
    if len(text) >= 32 and re.fullmatch(r"[0-9a-f]+", text, re.IGNORECASE) and _switches(text, digits=True) >= 2:
        return True  # hex hash
    if len(text) < 20:
        return False
    runs = [r for r in re.split(r"[-_.:~\s]", text) if r]
    if not runs:
        return False  # "-----" is a divider line, not a token
    token_runs = [r for r in runs if _tokenish(r)]
    return any(len(r) >= 8 for r in token_runs) or len(token_runs) * 2 >= len(runs)


# --- one URL -------------------------------------------------------------------

def _value_is_sensitive(value: str) -> bool:
    decoded = _decode(value)
    return ("@" in decoded or decoded.startswith("eyJ") or looks_random(value)
            or (decoded != value and looks_random(decoded)))


def _is_tracking_name(name: str) -> bool:
    low = name.lower()
    return low in TRACKING_PARAMS or low.startswith(TRACKING_PREFIXES) or bool(SECRET_PARAM.search(low))


def is_footer(text: str, url: str | None) -> bool:
    if FOOTER_TEXT.fullmatch(text.strip()):
        return True
    try:
        return bool(url) and bool(FOOTER_PATH.search(urlsplit(url).path))
    except ValueError:
        return False


def _bad_segment(segment: str, host: str, sensitive_page: bool) -> str | None:
    """Why a path (or fragment-route) segment makes the link unsafe, or None."""
    decoded = _decode(segment)
    if "@" in decoded and ADDRESS.search(decoded):
        if not (_domain_match(host, ("teams.microsoft.com",)) and TEAMS_THREAD.fullmatch(decoded)):
            return "address-in-path"
    if UUID.fullmatch(decoded):
        return None if _domain_match(host, JOB_BOARD_DOMAINS) and not sensitive_page else "token-path"
    if looks_random(segment) and (sensitive_page or not _domain_match(host, CONTENT_ID_DOMAINS)):
        return "random-path"
    return None


def _clean_nested(value: str, base: str, depth: int) -> str | None | bool:
    """A parameter value that is itself a URL or path: clean it like a URL.

    Returns the cleaned value, None to drop the parameter, or False when the
    value is not a URL at all (judge it as a plain value instead). Handles
    absolute ("https://x"), protocol-relative ("//x"), "www.x", double-encoded
    ("https%3A%2F%2F..."), and relative ("/verify?otp=1", "verify#code=1")
    targets. A relative target stays relative, so the app still accepts it.
    """
    value = value.strip()
    if re.match(r"(?i)https?%3a", value):
        value = unquote(value)  # was encoded twice
    # Browsers read leading backslashes as slashes: "\\x.com/a" means "//x.com/a"
    value = re.sub(r"^\\+", lambda m: "/" * len(m.group(0)), value)
    absolute = re.match(r"(?i)(https?://|//|www\.)", value)
    relative = value.startswith("/") or (("?" in value or "#" in value) and "=" in value)
    if not (absolute or relative):
        return False
    target = "http://" + value if value.lower().startswith("www.") else value
    try:
        inner = clean_url(urljoin(base, target), depth + 1).url
    except ValueError:  # odd input like "https://[SITE_URL]/": drop it, never crash
        return None
    if inner is None or absolute:
        return inner
    parts = urlsplit(inner)
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def _decide_param(raw: str, base: str, host: str, depth: int) -> str | None:
    """Return the "name=value" piece to keep (possibly rewritten), or None to drop it."""
    if depth > MAX_NESTING:
        return None
    name_raw, has_eq, value_raw = raw.partition("=")
    name, value = unquote_plus(name_raw), unquote_plus(value_raw)
    low = name.lower()
    if _domain_match(host, PATH_ONLY_DOMAINS):
        return raw if low in PATH_ONLY_KEEP and not _value_is_sensitive(value) else None
    if _is_tracking_name(name) or _has_address(name_raw):
        return None
    if set(value_raw) <= {"="}:  # no value: a bare key, or base64 with "=" padding
        # often a value in disguise: a token, an address
        return None if looks_random(name_raw) else raw
    if any(low in names for domain, names in HOST_DROP_PARAMS.items() if _domain_match(host, (domain,))):
        return None
    if low == "r" and re.match(r"/p/[^/]+", urlsplit(base).path):
        return None  # Substack's reader ID, also on custom domains (posts live at /p/<slug>)
    nested = _clean_nested(value, base, depth)
    if nested is not False:
        return f"{name_raw}={quote(nested, safe='')}" if nested else None
    if _domain_match(host, CONTENT_ID_DOMAINS) and low in CONTENT_ID_PARAMS and "@" not in _decode(value):
        return raw  # a document or video ID, not a reader ID
    if _value_is_sensitive(value):
        return None
    if len(name) == 1 and low not in "qpv" and len(value) >= 5 and _switches(value, digits=True) >= 1:
        return None  # "?c=8F3kQ2", "?r=2bjtip": a code; q/p/v are searches, posts, videos
    return raw


def _clean_fragment(fragment: str, host: str, sensitive_page: bool, base: str, depth: int) -> str:
    """Keep plain anchors (#section) and app routes (#/events/12); drop the rest."""
    if re.fullmatch(r"[\w-]{1,64}", fragment):
        return "" if looks_random(fragment) else fragment
    if fragment[:1] in ("/", "!"):
        route, _, query = fragment.partition("?")
        segments = [s for s in route.lstrip("!").split("/") if s]
        sensitive = sensitive_page or any(SENSITIVE_ROUTE.fullmatch(_decode(s)) for s in segments)
        if any(_bad_segment(s, host, sensitive) for s in segments):
            return ""
        kept = [p for p in (_decide_param(r, base, host, depth) for r in query.split("&") if r) if p]
        return route + ("?" + "&".join(kept) if kept else "")
    return ""


def clean_url(url: str, depth: int = 0) -> Verdict:
    """Judge one URL. Never raises: email is untrusted input, and one odd link
    must not stop a whole sync."""
    if len(url) > MAX_INPUT_CHARS:
        return Verdict(None, "too-long")  # before any regex: keeps every check linear
    try:
        return _clean_url(url, depth)
    except ValueError:
        return Verdict(None, "unparseable")


def _clean_url(url: str, depth: int) -> Verdict:
    if depth > MAX_NESTING:
        return Verdict(None, "nested-too-deep")
    url = re.sub(r"[\t\r\n]", "", url).strip().replace("&amp;", "&").replace("&#38;", "&")
    if url.lower().startswith("www."):
        url = "http://" + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return Verdict(None, "unparseable")
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return Verdict(None, "not-web")
    host = parts.hostname.lower()
    params = [p for p in parts.query.replace(" ", "%20").split("&") if p]

    # 1. Wrappers: the real destination is a parameter; clean that instead.
    for domain, path_part, names in REDIRECTORS:
        if _domain_match(host, (domain,)) and path_part in parts.path:
            for raw in params:
                name, _, value = raw.partition("=")
                if name.lower() in names and re.match(r"(?i)https?(://|%3A%2F%2F)", value):
                    return clean_url(unquote_plus(value), depth + 1)

    # 2. Trackers and token-shaped paths: nothing worth keeping.
    if (_domain_match(host, TRACKER_DOMAINS) or host.startswith(TRACKER_HOST_PREFIXES)
            or any(p.partition("=")[0].lower() in TRACKER_PARAMS for p in params)):
        return Verdict(None, "tracker")
    path = parts.path
    if host.endswith("facebook.com") and path.rstrip("/") == "/n":
        # facebook.com/n/?groups%2F123%2Fposts%2F456%2F&aref=...: the first key IS the path
        first = unquote_plus(params[0]) if params else ""
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", first):
            return Verdict(None, "tracker")
        path, params = "/" + first.lstrip("/"), []
    segments = [s for s in path.split("/") if s]
    sensitive_page = any(SENSITIVE_ROUTE.fullmatch(_decode(s)) for s in segments)
    for segment in segments:
        reason = _bad_segment(segment, host, sensitive_page)
        if reason:
            return Verdict(None, reason)

    # 3. Parameters and fragment, judged piece by piece.
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    scheme = "https" if parts.scheme.lower() == "https" else "http"
    base = urlunsplit((scheme, netloc, path, "", ""))
    kept = [p for p in (_decide_param(raw, base, host, depth) for raw in params) if p]
    fragment = ("" if _domain_match(host, PATH_ONLY_DOMAINS)
                else _clean_fragment(parts.fragment, host, sensitive_page, base, depth))
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    cleaned = urlunsplit((scheme, netloc, path, "&".join(kept), fragment))
    if len(cleaned) > MAX_URL_CHARS:
        return Verdict(None, "too-long")
    return Verdict(cleaned, "kept")


# --- body text -----------------------------------------------------------------

def link_marker(text: str, href: str | None) -> str:
    """What the HTML extractor appends after a link's text: " <cleaned url>" or ""."""
    if not href or is_footer(text, href):
        return ""
    url = clean_url(href).url
    return f" <{url}>" if url else ""


def _split_trailing(url: str) -> tuple[str, str]:
    """Separate sentence punctuation from a URL; keep ')' when it is balanced."""
    end = len(url)
    opens, closes = url.count("("), url.count(")")
    while end:
        ch = url[end - 1]
        if ch in ".,;:!?'\"]”’»）】」］＞』》〉。":
            end -= 1
        elif ch == ")" and closes > opens:
            closes -= 1
            end -= 1
        else:
            break
    return url[:end], url[end:]


WRAPPED_VALUE = re.compile(r"(https?://\S*[?&#]([\w.-]{1,40})=)\s+(?:>\s*)*(\S+)", re.IGNORECASE)


def drop_wrapped_secrets(text: str) -> str:
    """A URL that ends in "otp=" / "token=" lost its value to a line wrap (or a
    space, after HTML or snippet whitespace collapsing): drop the next word,
    which is that value. Neutral names ("q=") keep their next word."""
    return WRAPPED_VALUE.sub(lambda m: m.group(1) if _is_tracking_name(m.group(2)) else m.group(0), text)


def clean_text_urls(text: str, seen: set[str]) -> str:
    """Clean every URL in finished body text. Each kept URL appears once per email."""

    def replace(match: re.Match) -> str:
        lead = match.group(1)
        url, trailing = _split_trailing(match.group(2))
        if is_footer("", url):
            return trailing
        cleaned = clean_url(url).url
        if cleaned is None or cleaned in seen:
            return trailing
        seen.add(cleaned)
        return f"{lead}<{cleaned}>{trailing}"

    return URL_IN_TEXT.sub(replace, text)


def _scrub_piece(piece: str) -> str:
    """Judge a stretch of text that sits outside our cleaned links."""
    if not piece:
        return piece
    if len(piece) > MAX_WORD_CHARS:
        return ""
    core = piece.strip("()[]<>.,;:!?'\"“”‘’")
    if HASHTAG.fullmatch(core):
        return piece  # hashtags and @mentions are content
    if "=" in core:
        for chunk in re.split(r"[&?;#]", core):
            names = ASSIGNMENT.findall(chunk)
            value = chunk.partition("=")[2]
            if any(_is_tracking_name(n) for n in names) or (names and value and _value_is_sensitive(value)):
                return ""
    # "COMP1010/COMP2020/INFO3001": judge each listed item, not the whole run
    if any(len(part) >= 20 and looks_random(part) for part in re.split(r"[/,]", core)):
        return ""
    return piece


def scrub_residue(text: str) -> str:
    """Safety net after URL cleaning: drop query-string leftovers and stray tokens.

    Catches the tail of a URL cut by a space or line break ("Team&lipi=...&
    midToken=..."), a lone "token=SECRET", and random tokens in text. Our own
    cleaned links are cut out first and put back untouched, so a link glued
    to punctuation ("(<https://...>)") is never judged as residue.
    """

    def fix(match: re.Match) -> str:
        word = match.group(0)
        out, last = [], 0
        for link in OUR_LINK.finditer(word):
            out.append(_scrub_piece(word[last:link.start()]))
            out.append(link.group(0))
            last = link.end()
        out.append(_scrub_piece(word[last:]))
        return "".join(out)

    return re.sub(r"\S+", fix, text)


# --- the audit -------------------------------------------------------------------

def audit(urls: list[str]) -> list[dict]:
    """Per-host summary of what cleaning does: counts and parameter NAMES only.

    Values are never collected, so the report can't leak a token. Bare keys
    (no "=") and names that look like tokens are masked too: Facebook hides
    the destination, often a person's profile, in a bare key.
    """
    hosts: dict[str, dict] = collections.defaultdict(
        lambda: {"links": 0, "verdicts": collections.Counter(),
                 "dropped_params": collections.Counter(), "kept_params": collections.Counter()}
    )
    for url in urls:
        try:
            parts = urlsplit(url.strip().replace("&amp;", "&"))
        except ValueError:
            continue
        verdict = clean_url(url)
        host = (parts.hostname or f"<{parts.scheme or 'none'}>").lower()
        summary = hosts[host]
        summary["links"] += 1
        summary["verdicts"][verdict.reason] += 1
        for raw in (p for p in parts.query.split("&") if p):
            name, has_eq, _ = raw.partition("=")
            name = unquote_plus(name)
            ordinary = has_eq and PARAM_NAME.fullmatch(name) and not looks_random(name)
            shown = name if ordinary else "<bare key>"
            keep = verdict.url is not None and _decide_param(raw, url, host, 0) is not None
            (summary["kept_params"] if keep else summary["dropped_params"])[shown] += 1
    return [{"host": h, **s} for h, s in sorted(hosts.items(), key=lambda kv: -kv[1]["links"])]
