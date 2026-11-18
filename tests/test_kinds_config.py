"""The [kinds] section: the owner's own senders, kept in config.toml so the public code never names them.

Run: pytest
"""

import pytest
from test_api_config import write_config

from assistant.email_kinds import KindSenders
from assistant.errors import ConfigError


def test_no_kinds_section_means_no_owner_senders(tmp_path):
    assert write_config(tmp_path).kinds == KindSenders()


def test_the_kinds_section_lists_the_owners_senders(tmp_path):
    cfg = write_config(tmp_path, '[kinds]\nstudy_domains = ["Uni.Example.edu"]\nstudy_sender_words = ["Program Office"]\n'
                                 'promo_domains = ["newsletter.example.com"]\nhackathon_words = ["example jam"]\n')
    assert cfg.kinds == KindSenders(study_domains=("uni.example.edu",), study_sender_words=("Program Office",),
                                    promo_domains=("newsletter.example.com",), hackathon_words=("example jam",))


@pytest.mark.parametrize("section", [
    '[kinds]\nstudy_domain = ["uni.example.edu"]',  # a typo must not silently sort nothing
    '[kinds]\nstudy_domains = "uni.example.edu"',
    "[kinds]\npromo_domains = [5]",
    '[kinds]\npromo_domains = [""]',
])
def test_the_kinds_section_refuses_nonsense(tmp_path, section):
    with pytest.raises(ConfigError):
        write_config(tmp_path, section)
