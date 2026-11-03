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


class SecretError(AssistantError):
    """An API key is missing or doesn't look like one. Never includes the key itself."""


class CapReached(AssistantError):
    """A spending cap blocked a model call. Raised, not returned, so a caller
    can't ignore it and make the call anyway."""

    def __init__(self, reason: str) -> None:
        self.reason = reason  # "daily-cap" or "monthly-cap"
        super().__init__(f"LLM spending cap reached ({reason}); see `assistant costs`.")


class EngineRunning(AssistantError):
    """engine.lock is held, by the engine or by a CLI command that writes: a second
    writer would race it (and pay twice for the same emails)."""

    def __init__(self, holder: str = "engine") -> None:
        self.holder = holder
        if holder == "engine":
            super().__init__("The Pocket Assistant engine is running. Use the dashboard, or stop the engine first.")
        else:
            super().__init__(f"`assistant {holder}` is still running in another window. "
                             "Try again when it finishes.")


class PairingError(AssistantError):
    """A pairing code that can't be used. The message never repeats the code."""
