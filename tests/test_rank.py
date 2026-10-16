"""Dedupe keys and scoring (pure). The profile mirrors your real weights.

Run: pytest
"""

import pytest

from assistant.models import OPPORTUNITY_KINDS
from assistant.profile import Profile
from assistant.rank import Opportunity, explain, keys, merge, org_key, score, title_key, url_key, words

PROFILE = Profile(
    interests=("Agentic AI", "LLM apps"), level="student", home="HCMC", outside_home=-30, remote_ok="vietnam",
    open_to=tuple(k for k in OPPORTUNITY_KINDS if k != "other"),
    boost={"AI": 20, "HCMC": 20, "FPT": 15, "Internship": 20, "Fresher": 10, "On-site": 10, "Remote": 5,
           "Middle": 5},
    penalize={"Sales": -20, "Tester": -10},
    aliases={"AI": ("trí tuệ nhân tạo",), "HCMC": ("Ho Chi Minh City", "TP.HCM", "Sài Gòn"),
             "Internship": ("intern", "thực tập sinh"), "On-site": ("onsite",), "Sales": ("bán hàng",)},
)


def opp(**changes) -> Opportunity:
    base = dict(kind="internship", title="Backend Developer", organizer="Example Co", deadline=None,
                location=None, location_scope="unknown", eligibility=None, prize_or_pay=None, url=None,
                relevance=50, why="fits")
    return Opportunity(**{**base, **changes})


def points(**changes) -> int:
    return score(opp(**changes), PROFILE)[0]


# --- normalizing and keys --------------------------------------------------------------

def test_vietnamese_accents_and_punctuation_fold_away():
    assert words("Thực tập sinh AI - TP.HCM") == ["thuc", "tap", "sinh", "ai", "tp", "hcm"]
    assert words("Đà Nẵng") == words("da nang") == ["da", "nang"]


def test_title_word_order_doesnt_matter_but_the_year_does():
    assert title_key("AI Hackathon 2026") == title_key("Hackathon AI, 2026")
    assert title_key("Hackathon 2025") != title_key("Hackathon 2026")


@pytest.mark.parametrize("name", [
    "Công ty TNHH ABC", "ABC Co., Ltd", "CÔNG TY TNHH MTV ABC", "Công ty Cổ phần ABC", "ABC JSC", "abc",
])
def test_legal_forms_are_stripped_from_both_ends(name):
    assert org_key(name) == "abc"


def test_legal_form_stripping_keeps_real_names():
    assert org_key("Công ty Cổ phần Tập đoàn Masan") == "masan"
    assert org_key("Co.op Mart") == "co op mart"     # "Co." is a legal form only at the end


@pytest.mark.parametrize("a, b", [
    ("Masan Group", "Tập đoàn Masan"),
    ("ABC Joint Stock Company", "ABC JSC"),
    ("ABC One Member Limited Liability Company", "Công ty TNHH MTV ABC"),
])
def test_english_and_vietnamese_legal_forms_agree(a, b):
    assert org_key(a) == org_key(b)


def test_an_organizer_made_only_of_legal_words_names_nobody():
    assert org_key("Công ty TNHH") == org_key("Ltd") == ""
    assert keys(opp(organizer="Công ty TNHH", url=None), "m3")[0].endswith("|msg:m3")


def test_without_an_organizer_the_link_then_the_email_stands_in():
    a = opp(organizer=None, url="https://www.Jobs.example.com/post/1/?utm_source=mail", title="Tuyển thực tập sinh AI")
    b = opp(organizer=None, url="https://jobs.example.com/post/2", title="Tuyển thực tập sinh AI")
    assert keys(a, "m1")[0] != keys(b, "m1")[0]                  # two unnamed companies don't merge
    assert keys(a, "m1")[0].endswith("|jobs.example.com/post/1")  # the tracking parameter is gone
    assert keys(opp(organizer=None), "m7")[0].endswith("|msg:m7")
    assert url_key("https://EXAMPLE.com/a/") == "example.com/a"


def test_a_link_query_that_names_the_item_is_kept():
    one, two = url_key("https://careers.example.com/jobs/view?jobId=1"), url_key("https://careers.example.com/jobs/view?jobId=2")
    assert one != two and one == "careers.example.com/jobs/view?jobId=1"
    assert url_key("https://x.example.com/a?b=2&a=1&fbclid=z") == url_key("https://x.example.com/a?a=1&b=2")


def test_cpp_csharp_and_c_are_different_jobs():
    assert len({title_key("C++ Developer Intern"), title_key("C# Developer Intern"),
                title_key("C Developer Intern"), title_key(".NET Developer Intern")}) == 4


def test_the_organizers_name_inside_the_title_doesnt_split_a_job():
    assert title_key("Software Engineer Intern - FPT Software", "FPT Software") == \
        title_key("Software Engineer Intern", "FPT Software")
    assert title_key("FPT Hackathon", "FPT") == "hackathon"


def test_the_deadline_is_part_of_the_dedupe_key_only():
    base, dedupe = keys(opp(deadline="2026-11-20"), "m1")
    assert dedupe == f"{base}|2026-11-20"
    assert keys(opp(), "m1")[1] == f"{base}|nodate"


def test_a_second_sighting_fills_gaps_and_keeps_the_better_relevance():
    first = opp(relevance=60, why="ok", url=None, location_scope="unknown", kind="other")
    second = opp(relevance=80, why="great", url="https://x.example.com", deadline="2026-11-01",
                 location_scope="hcmc", organizer="Other name", kind="internship")
    merged = merge(first, second)
    assert (merged.relevance, merged.why, merged.url, merged.deadline) == (80, "great", "https://x.example.com",
                                                                          "2026-11-01")
    assert merged.kind == "internship"  # the kind goes with the clearer sighting's why
    assert (merged.organizer, merged.location_scope) == ("Example Co", "hcmc")  # only gaps are filled


# --- scoring ---------------------------------------------------------------------------------

def test_signed_weights_add_up():
    value, parts = score(opp(title="AI Sales Intern", relevance=50), PROFILE)
    assert value == 50 + 20 - 20 + 20
    assert explain(parts) == "relevance 50, +20 AI, +20 Internship, -20 Sales"


def test_keywords_match_whole_words_only():
    assert points(title="Salesforce Developer") == 50      # not "Sales"
    assert points(title="Retail Assistant") == 50          # "ai" inside a word is nothing
    assert points(title="Software Tester") == 40


def test_ai_in_capitals_is_ai_but_the_vietnamese_word_ai_is_not():
    assert points(title="Ai cũng có thể tham gia") == 50   # "anyone can join"
    assert points(title="Kỹ sư AI") == 70
    assert points(title="Kỹ sư trí tuệ nhân tạo") == 70    # an alias, matched without accents too


def test_aliases_count_once_per_keyword():
    assert points(title="Thực tập sinh / Intern Backend") == 70


def test_keywords_ignore_the_why_and_eligibility_text():
    assert points(why="Middle of the week, remote sensing", eligibility="Middle school AI fans") == 50
    assert points(title="Middle Backend Developer") == 55


@pytest.mark.parametrize("scope, location, expected", [
    ("hcmc", "Thu Duc", 70),                   # home: the HCMC boost, from the scope
    ("vietnam_other", "Hanoi", 20),            # outside home -30
    ("vietnam_other", "Hanoi office, TP.HCM candidates welcome", 20),  # the word alone earns nothing
    ("remote_vietnam", None, 50),              # remote within Vietnam is fine for you
    ("remote_international", None, 20),        # remote abroad is not
    ("abroad", "Sydney", 20),
    ("unknown", None, 50),
])
def test_the_location_rule_comes_from_the_scope(scope, location, expected):
    assert points(location_scope=scope, location=location) == expected


def test_kinds_youre_not_open_to_sink():
    assert points(kind="other") == 0               # 50 - 50


def test_penalties_still_count_among_the_best_items():
    # Capped at 100, these three tied, and the HCMC one could come last
    at_home = points(title="AI Engineer Intern", relevance=85, location_scope="hcmc")
    in_hanoi = points(title="AI Engineer Intern", relevance=85, location_scope="vietnam_other")
    other_kind = points(title="AI Engineer Intern", relevance=85, location_scope="hcmc", kind="other")
    assert at_home == 145 and at_home > in_hanoi == 95 and at_home > other_kind == 95
    assert points(title="Sales Tester", relevance=5, location_scope="abroad") == -55


def test_a_boost_in_the_location_of_an_unwanted_place_doesnt_count():
    assert points(location="On-site in Hanoi", location_scope="vietnam_other") == 20      # not +10 On-site
    assert points(location="Remote (Singapore)", location_scope="remote_international") == 20
    assert points(title="On-site Backend Developer", location_scope="hcmc") == 80          # in the title it counts


def test_when_the_model_couldnt_tell_the_location_text_can_still_say_home():
    value, parts = score(opp(location="Quận 1, TP.HCM", location_scope="unknown"), PROFILE)
    assert value == 70 and parts[-1] == ("at home (HCMC, from the location text)", 20)


def test_plurals_match_too():
    assert points(title="Summer Internships 2027") == 70
    assert points(title="AI Interns wanted") == 90
