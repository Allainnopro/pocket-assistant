"""Token storage tests: no network, no account, no real Credential Manager.

keyring is swapped for a dict (the `vault` fixture in conftest.py) and
Credentials.refresh for a fake, so these run offline and never touch
Windows Credential Manager.

Run: pytest
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from assistant import gmail_client
from assistant.errors import GmailAuthError

# Windows Credential Manager: 2560-byte blob, written as UTF-16 by keyring
WINDOWS_MAX_CHARS = 2560 // 2


def worst_case_creds() -> Credentials:
    # Google's documented maximums: access token 2048 bytes, refresh token 512 bytes
    return Credentials(
        token="ya29." + "a" * 2043,
        refresh_token="1//" + "r" * 509,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="1234567890-" + "c" * 32 + ".apps.googleusercontent.com",
        client_secret="GOCSPX-" + "s" * 28,
        scopes=gmail_client.SCOPES,
        expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )


def stored(vault) -> dict:
    return json.loads(vault[(gmail_client.KEYRING_SERVICE, gmail_client.KEYRING_USER)])


def test_saved_token_skips_access_token_and_fits_windows_limit(vault):
    gmail_client._save_token(worst_case_creds())
    raw = vault[(gmail_client.KEYRING_SERVICE, gmail_client.KEYRING_USER)]

    assert len(raw) <= WINDOWS_MAX_CHARS
    saved = json.loads(raw)
    assert "token" not in saved and "expiry" not in saved
    assert saved["refresh_token"].startswith("1//")


def test_get_credentials_mints_access_token_from_stored_refresh_token(vault, monkeypatch):
    gmail_client._save_token(worst_case_creds())
    calls = []

    def fake_refresh(self, request):
        calls.append(request)
        self.token = "fresh-access-token"
        self._refresh_token = "1//rotated"  # Google may rotate it

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    creds = gmail_client.get_credentials()

    assert len(calls) == 1
    assert creds.token == "fresh-access-token"
    assert stored(vault)["refresh_token"] == "1//rotated"  # latest one kept
    assert "token" not in stored(vault)


def test_get_credentials_explains_refresh_failure(vault, monkeypatch):
    gmail_client._save_token(worst_case_creds())

    def fake_refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    with pytest.raises(GmailAuthError, match="Token refresh failed.*assistant auth"):
        gmail_client.get_credentials()


def test_get_credentials_without_login(vault):
    with pytest.raises(GmailAuthError, match="Not logged in"):
        gmail_client.get_credentials()



def test_the_saved_sign_in_has_a_fingerprint_that_changes_with_it():
    import keyring

    from assistant.gmail_client import KEYRING_SERVICE, KEYRING_USER, token_fingerprint

    assert token_fingerprint() is None  # not signed in
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, '{"refresh_token": "first"}')
    first = token_fingerprint()
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, '{"refresh_token": "second"}')
    assert first is not None and token_fingerprint() not in (None, first)
    assert b"first" not in first  # a hash, never the token
