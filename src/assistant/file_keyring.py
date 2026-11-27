"""A keyring backend that keeps secrets in a private JSON file, for a headless Linux box.

Windows keeps Credential Manager. A server has no keyring service, so vault.py
and gmail_imap.py (which keep calling `keyring`) get this one instead, installed
by the CLI at start. POSIX only (os.fchmod).

The file is {"<service>": {"<user>": "<secret>"}}, mode 0600, next to the
database. A file that group or others can touch is refused, and the message
names the fix but never a secret. Failures are AssistantError, so the CLI
prints a message instead of a traceback.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import keyring
import keyring.backend
import keyring.errors

from .config import Config
from .errors import AssistantError


class FileKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]  # only ever installed explicitly

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            mode = self.path.stat().st_mode
        except FileNotFoundError:
            return {}
        if mode & 0o077:
            raise AssistantError(
                f"{self.path} can be read by other users, so the secrets in it are not safe. "
                f"Run: chmod 600 {self.path}"
            )
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            raise AssistantError(f"{self.path} is not valid JSON.") from None
        except PermissionError:
            raise AssistantError(
                f"The engine's user can't read {self.path}. Run: chown pocket:pocket {self.path}"
            ) from None

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".secrets-")
        except PermissionError:
            raise AssistantError(
                f"The engine's user can't write in {self.path.parent}. Run: chown pocket:pocket {self.path.parent}"
            ) from None
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get_password(self, service: str, username: str) -> str | None:
        return self._read().get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        data = self._read()
        data.setdefault(service, {})[username] = password
        self._write(data)

    def delete_password(self, service: str, username: str) -> None:
        data = self._read()
        if username not in data.get(service, {}):
            raise keyring.errors.PasswordDeleteError("No such secret saved.")
        del data[service][username]
        if not data[service]:
            del data[service]
        self._write(data)


def install_for(cfg: Config) -> None:
    if sys.platform == "win32":
        return
    keyring.set_keyring(FileKeyring(cfg.db_path.parent / "secrets.json"))
