"""CLI error-handling tests: expected failures exit cleanly, from one place.

Run: pytest
"""

import pytest

from assistant.cli import main
from assistant.config import load_config
from assistant.errors import AssistantError, ConfigError


def test_library_code_raises_a_catchable_error(tmp_path):
    # A plain Exception subclass, so a scheduler's `except Exception` can catch it
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path / "missing.toml")
    assert isinstance(info.value, Exception)
    assert isinstance(info.value, AssistantError)


def test_cli_turns_expected_failures_into_a_clean_exit(tmp_path):
    with pytest.raises(SystemExit) as info:
        main(["--config", str(tmp_path / "missing.toml"), "stats"])
    assert "Config file not found" in str(info.value.code)
