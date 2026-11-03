"""The [api] and [dashboard] settings.

Run: pytest
"""

import pytest

from assistant.config import load_config
from assistant.errors import ConfigError


def write_config(tmp_path, text=""):
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    return load_config(tmp_path / "config.toml")


def test_api_and_dashboard_settings_default_sensibly(tmp_path):
    cfg = write_config(tmp_path)
    assert (cfg.api.port, cfg.api.tailnet_host, cfg.api.tailnet_login, cfg.dashboard.for_you_min_score) == (
        47615, "", "", 60)


@pytest.mark.parametrize("section", [
    "[api]\nport = 80", "[api]\nport = 70000", "[api]\nport = true", '[api]\nport = "47615"',
    '[api]\ntailnet_host = "https://pc.tail1234.ts.net"', '[api]\ntailnet_host = "pc.example.com"',
    '[api]\ntailnet_host = "pc.tail1234.ts.net:443"', '[api]\ntailnet_host = "PC.tail1234.ts.net"',
    '[api]\ntailnet_host = "pc.tail1234.ts.net/x"', "[api]\ntailnet_host = 5",
    '[api]\ntailnet_login = "me"', "[api]\ntailnet_login = 5",
    "[dashboard]\nfor_you_min_score = 500", "[dashboard]\nfor_you_min_score = true",
    '[api]\nhost = "0.0.0.0"', "[dashboard]\nmin = 1",
])
def test_api_settings_refuse_nonsense(tmp_path, section):
    with pytest.raises(ConfigError):
        write_config(tmp_path, section)


def test_a_tailnet_name_is_kept(tmp_path):
    cfg = write_config(tmp_path, '[api]\ntailnet_host = "my-pc.tail1234.ts.net"\ntailnet_login = "me@gmail.com"\n')
    assert (cfg.api.tailnet_host, cfg.api.tailnet_login) == ("my-pc.tail1234.ts.net", "me@gmail.com")
