"""profile.toml loading: valid files load, mistakes fail loudly with a helpful message.

Run: pytest
"""

from pathlib import Path

import pytest

from assistant.errors import ConfigError
from assistant.profile import load_profile

REPO = Path(__file__).resolve().parents[1]

VALID = """
[me]
open_to = ["hackathon", "internship"]
[location]
home = "HCMC"
outside_home = -30
remote_ok = "vietnam"
[boost]
AI = 20
[penalize]
Sales = -20
[aliases]
HCMC = ["Sài Gòn"]
"""


def write(tmp_path, text):
    path = tmp_path / "profile.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_example_is_always_valid():
    profile = load_profile(REPO / "profile.example.toml")
    assert profile.boost and profile.penalize


def test_a_valid_profile_loads_with_spellings(tmp_path):
    profile = load_profile(write(tmp_path, VALID))
    assert profile.open_to == ("hackathon", "internship")
    assert profile.spellings("HCMC") == ("HCMC", "Sài Gòn")
    assert profile.remote_ok == "vietnam" and profile.outside_home == -30
    assert profile.drop_promotions is False


@pytest.mark.parametrize("broken, message", [
    (VALID.replace("Sales = -20", "Sales = 20"), "penalties must be negative"),
    (VALID.replace("AI = 20", "AI = -20"), "boosts must be between 1"),
    (VALID.replace('"internship"]', '"internships"]'), "unknown kinds"),
    (VALID.replace("[boost]", "[boosts]"), "unknown section"),
    (VALID.replace('HCMC = ["Sài Gòn"]', 'Saigon = ["Sài Gòn"]'), "match no keyword"),
    (VALID.replace('remote_ok = "vietnam"', 'remote_ok = "asia"'), "remote_ok must be one of"),
    (VALID.replace("outside_home = -30", "outside_home = 30"), "outside_home must be a whole number between"),
    (VALID.replace("AI = 20", 'AI = "20"'), "whole number"),
    (VALID + "\n[me\n", "not valid TOML"),
])
def test_mistakes_fail_loudly(tmp_path, broken, message):
    with pytest.raises(ConfigError, match=message):
        load_profile(write(tmp_path, broken))


def test_missing_profile_points_at_the_example(tmp_path):
    with pytest.raises(ConfigError, match="profile.example.toml"):
        load_profile(tmp_path / "profile.toml")


# --- review findings: every easy mistake fails loudly ----------------------------

@pytest.mark.parametrize("extra, message", [
    ('[filter]\ndrop_subject_patterns = "weekly digest"', "must be a list"),     # would split into letters
    ('[filter]\ndrop_subject_patterns = [""]', "empty entry"),                     # would match everything
    ('[filter]\ndrop_subject_patterns = ["ab"]', "at least 3 characters"),
    ('[filter]\ndrop_promotions = "false"', "true or false"),                    # "false" is truthy
    ('[filter]\ndrop_sender_domain = ["discord.com"]', "unknown key"),          # typo'd key
    ('[filter]\ndrop_sender_domains = ["slack"]', "not a domain"),
])
def test_filter_section_mistakes_fail_loudly(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_profile(write(tmp_path, VALID + "\n" + extra + "\n"))


@pytest.mark.parametrize("broken, message", [
    (VALID.replace("outside_home = -30", "outside_hom = -30"), "unknown key"),
    (VALID.replace('HCMC = ["Sài Gòn"]', 'HCMC = "Saigon"'), "must be a list"),
    ("filter = 5\n" + VALID, "must be a section"),
    (VALID.replace("AI = 20", "AI = 20\nai = 10"), "same keyword"),
    (VALID.replace("Sales = -20", "Sales = -20\nai = -5"), "both"),
    (VALID.replace('outside_home = -30', 'outside_home = "minus 30"'), "whole number"),
])
def test_more_mistakes_fail_loudly(tmp_path, broken, message):
    with pytest.raises(ConfigError, match=message):
        load_profile(write(tmp_path, broken))


def test_notepad_files_are_handled(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_bytes(b"\xef\xbb\xbf" + VALID.encode("utf-8"))  # "UTF-8 with BOM"
    assert load_profile(path).boost == {"AI": 20}
    path.write_bytes(VALID.encode("utf-16"))                   # "UTF-16 LE"
    with pytest.raises(ConfigError, match="Save As > Encoding: UTF-8"):
        load_profile(path)


def test_sender_domains_are_normalized(tmp_path):
    profile = load_profile(write(tmp_path, VALID + '\n[filter]\ndrop_sender_domains = [" noreply@Discord.com "]\n'))
    assert profile.drop_sender_domains == ("discord.com",)


def test_the_owners_real_profile_loads_when_present():
    real = REPO / "profile.toml"
    if real.exists():  # git-ignored, so absent on other machines
        load_profile(real)
