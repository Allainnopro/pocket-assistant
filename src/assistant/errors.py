"""Expected failures, as typed exceptions.

Why not SystemExit everywhere, like Phase 0 did? SystemExit is not an
Exception subclass, so `except Exception` cannot catch it. That was fine for
a CLI, but the Phase 1 scheduler runs several jobs in a row: one expired
Gmail token must not kill the digest or the privacy sweep that follow.

Rule: library code raises these; only cli.main turns them into an exit
message for the user.
"""

from __future__ import annotations


class AssistantError(Exception):
    """An expected failure whose message is written for the user."""


class ConfigError(AssistantError):
    """config.toml (or a file it points to) is missing or invalid."""


class GmailAuthError(AssistantError):
    """Not logged in, login failed, or the refresh token stopped working."""
