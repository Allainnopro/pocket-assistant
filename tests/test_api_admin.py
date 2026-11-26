"""Backups for the keeper device, and profile.toml edited over the API.

Run: pytest
"""

import hashlib
import queue
from datetime import date

from api_helpers import PASSWORD, Api, bearer

from assistant.engine.backup import backup_folder, make_backup

ORIGIN = {"Origin": "http://tauri.localhost"}


def make_api(tmp_path, keeper=False):
    """(api, device key); the paired device is the keeper when asked."""
    api = Api(tmp_path, "[backup]\nkeeper_device = 1\n" if keeper else "")
    return api, api.pair()[1]


def test_only_the_keeper_gets_the_backup(tmp_path):
    api, key = make_api(tmp_path, keeper=True)
    other = api.pair("phone")[1]
    path = make_backup(api.cfg.db_path, backup_folder(api.cfg), date(2026, 11, 27))
    r = api.client.get("/api/backup/latest", headers=bearer(key))
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert r.content == path.read_bytes()
    assert r.headers["X-Backup-SHA256"] == hashlib.sha256(r.content).hexdigest()
    assert r.headers["X-Backup-Date"] == "2026-11-27"
    assert api.client.get("/api/backup/latest", headers=bearer(other)).status_code == 403
    assert api.client.get("/api/backup/latest", headers=bearer(PASSWORD)).status_code == 403
    api.close()


def test_no_keeper_set_means_nobody_gets_it(tmp_path):
    api, key = make_api(tmp_path)
    make_backup(api.cfg.db_path, backup_folder(api.cfg), date(2026, 11, 27))
    assert api.client.get("/api/backup/latest", headers=bearer(key)).status_code == 403
    api.close()


def test_no_backup_yet_is_404(tmp_path):
    api, key = make_api(tmp_path, keeper=True)
    assert api.client.get("/api/backup/latest", headers=bearer(key)).status_code == 404
    api.close()


def test_backup_headers_are_readable_from_the_app(tmp_path):
    api, key = make_api(tmp_path, keeper=True)
    make_backup(api.cfg.db_path, backup_folder(api.cfg), date(2026, 11, 27))
    r = api.client.get("/api/backup/latest", headers={**bearer(key), **ORIGIN})
    exposed = r.headers["access-control-expose-headers"]
    assert "X-Backup-Date" in exposed and "X-Backup-SHA256" in exposed
    api.close()


def test_profile_round_trip_sends_profile_changed(tmp_path):
    api, key = make_api(tmp_path)
    events = api.bus.subscribe()
    text = '[me]\nopen_to = ["internship"]\n'
    r = api.client.put("/api/profile/text", json={"text": text}, headers=bearer(key))
    assert r.status_code == 200 and r.json() == {"text": text}
    assert api.cfg.profile_path.read_bytes().decode() == text
    assert events.get_nowait()["kind"] == "profile_changed"
    assert api.client.get("/api/profile/text", headers=bearer(key)).json() == {"text": text}
    api.close()


def test_a_broken_profile_is_refused_and_the_old_one_kept(tmp_path):
    api, key = make_api(tmp_path)
    before = api.cfg.profile_path.read_bytes()
    events = api.bus.subscribe()
    r = api.client.put("/api/profile/text", json={"text": "interests = ["}, headers=bearer(key))
    assert r.status_code == 400 and r.json()["detail"]
    assert api.cfg.profile_path.read_bytes() == before
    assert not (tmp_path / "profile.toml.new").exists()
    assert events.empty()
    api.close()


def test_a_profile_over_64_kb_is_refused(tmp_path):
    api, key = make_api(tmp_path)
    before = api.cfg.profile_path.read_bytes()
    r = api.client.put("/api/profile/text", json={"text": "#" * 65_537}, headers=bearer(key))
    assert r.status_code == 413
    assert api.cfg.profile_path.read_bytes() == before
    api.close()


def test_the_limit_counts_bytes_not_characters(tmp_path):
    api, key = make_api(tmp_path)
    text = "#" + "đ" * 32_768  # 32,769 characters, 65,537 bytes
    r = api.client.put("/api/profile/text", json={"text": text}, headers=bearer(key))
    assert r.status_code == 413
    api.close()


def test_other_paths_keep_the_small_body_limit(tmp_path):
    api, key = make_api(tmp_path)
    r = api.client.post("/api/pair", json={"code": "x", "device_name": "y" * 5000})
    assert r.status_code == 413
    api.close()


def test_put_passes_the_cors_preflight(tmp_path):
    api, _ = make_api(tmp_path)
    r = api.client.options("/api/profile/text", headers={**ORIGIN, "Access-Control-Request-Method": "PUT",
                                                         "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200 and "PUT" in r.headers["access-control-allow-methods"]
    api.close()
