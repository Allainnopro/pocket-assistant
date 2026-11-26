"""The nightly backup, for the one device that keeps copies, and profile.toml over the API."""

from __future__ import annotations

import hashlib
import os
import threading

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..errors import ConfigError
from ..profile import load_profile
from .api_deps import Authorized, Ctx
from .backup import backup_folder, latest
from .events import PROFILE_CHANGED

router = APIRouter()

MAX_PROFILE_BYTES = 65_536
_writing = threading.Lock()  # one fixed staged name: two PUTs at once must not mix


class ProfileText(BaseModel):
    text: str


@router.get("/api/backup/latest", response_class=FileResponse)
def newest_backup(who: Authorized, ctx: Ctx) -> FileResponse:
    keeper = ctx.cfg.backup.keeper_device
    if who.kind != "device" or keeper is None or who.device_id != keeper:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the device that keeps backups can download them.")
    found = latest(backup_folder(ctx.cfg))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No backup yet.")
    day, path = found
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return FileResponse(path, media_type="application/gzip",
                        headers={"X-Backup-Date": day.isoformat(), "X-Backup-SHA256": digest.hexdigest()})


@router.get("/api/profile/text")
def read_profile(_: Authorized, ctx: Ctx) -> ProfileText:
    try:
        return ProfileText(text=ctx.cfg.profile_path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile.toml yet.") from None
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "profile.toml isn't valid UTF-8.") from None


@router.put("/api/profile/text")
def write_profile(body: ProfileText, _: Authorized, ctx: Ctx) -> ProfileText:
    data = body.text.encode("utf-8")
    if len(data) > MAX_PROFILE_BYTES:
        raise HTTPException(413, "profile.toml can be at most 64 KB.")
    target = ctx.cfg.profile_path
    staged = target.with_name(target.name + ".new")
    with _writing:
        try:
            staged.write_bytes(data)
            try:
                load_profile(staged)
            except ConfigError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc).replace(staged.name, target.name)) from None
            os.replace(staged, target)  # the engine's profile watcher re-ranks within 30 s
        finally:
            staged.unlink(missing_ok=True)
    ctx.bus.publish(PROFILE_CHANGED, {})
    return body
