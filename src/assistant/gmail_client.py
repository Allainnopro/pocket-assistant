"""Gmail API access: OAuth + listing + fetching. Read-only by design.

Security decisions, explained:
- SCOPE is gmail.readonly. Even if a malicious email tricks the LLM later,
  the token physically cannot send, delete or modify mail.
- The OAuth token is stored in the OS keychain (Windows Credential Manager)
  via `keyring`, not in token.json on disk where it could be committed or
  read by any program scanning your folders.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import keyring
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
KEYRING_SERVICE = "pocket-assistant"
KEYRING_USER = "gmail-oauth-token"


def _save_token(creds: Credentials) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, creds.to_json())


def _load_token() -> Credentials | None:
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if not raw:
        return None
    return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)


def login(credentials_file: Path) -> Credentials:
    """Interactive first-time login: opens a browser for Google consent."""
    if not credentials_file.exists():
        raise SystemExit(
            f"OAuth client file not found: {credentials_file}\n"
            "Google Cloud Console -> APIs & Services -> Credentials -> "
            "Create OAuth client ID (Desktop app) -> download as credentials.json"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)  # port=0: pick any free local port
    _save_token(creds)
    return creds


def get_credentials() -> Credentials:
    """Load the saved token, refreshing it if expired. Never opens a browser.

    Access tokens last about an hour; the refresh token gets a new one
    silently. That is what lets a scheduled background sync run unattended.
    """
    creds = _load_token()
    if creds is None:
        raise SystemExit("Not logged in. Run: assistant auth")

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # Happens if you revoke access, or the app is in "Testing"
                # mode in Google Cloud (refresh tokens expire after 7 days there).
                raise SystemExit(f"Token refresh failed ({exc}). Run: assistant auth") from exc
            _save_token(creds)
        else:
            raise SystemExit("Saved token is invalid. Run: assistant auth")
    return creds


def build_service(creds: Credentials):
    # cache_discovery=False avoids a noisy warning with recent google-auth versions
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def iter_message_ids(service, query: str, limit: int) -> Iterator[str]:
    """Yield message ids matching a Gmail search query, following pagination.

    messages.list returns at most 500 ids per page plus a nextPageToken.
    It returns ids only, which is cheap; we fetch full bodies separately and
    only for messages we haven't stored yet.
    """
    page_token = None
    yielded = 0
    while yielded < limit:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=min(500, limit - yielded))
            .execute()
        )
        for m in resp.get("messages", []):
            yield m["id"]
            yielded += 1
            if yielded >= limit:
                return
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def get_message(service, message_id: str) -> dict:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()
