"""Who may reach the local API, and from where.

Run: pytest
"""

import json

import pytest
from api_helpers import NOW, PASSWORD, TAILNET, Api, api, bearer, dummy_ctx, free_port, tailnet_api  # noqa: F401

from assistant import __version__
from assistant.engine.api import create_app
from assistant.engine.devices import device_for_key, new_pairing_code

VIA_TAILNET = {"Host": TAILNET}


def test_health_needs_no_password_and_says_nothing_private(api):
    assert api.client.get("/api/health").json() == {"ok": True, "version": __version__}


def test_building_the_app_reads_nothing():
    assert create_app(dummy_ctx()).openapi()["info"]["title"] == "Pocket Assistant"


@pytest.mark.parametrize("host", ["evil.example.com", "127.0.0.1:8080", "127.0.0.1", "localhost:47615", TAILNET])
def test_an_unknown_host_is_refused_before_anything_else(api, host):
    r = api.client.get("/api/health", headers={"Host": host})
    assert (r.status_code, r.text) == (400, "Invalid host header")


def test_the_tailnet_name_is_a_known_host_once_configured(tailnet_api):
    assert tailnet_api.client.get("/api/health", headers=VIA_TAILNET).status_code == 200
    assert tailnet_api.client.get("/api/health", headers={"Host": TAILNET + ":8443"}).status_code == 400


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic " + PASSWORD, "Bearer ", "Bearer " + PASSWORD + "x"])
def test_routes_refuse_a_missing_or_wrong_secret(api, auth):
    r = api.client.get("/api/devices", headers={"Authorization": auth} if auth else {})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_the_launch_password_opens_this_pcs_own_routes(api):
    assert api.client.get("/api/devices", headers=bearer(PASSWORD)).json() == {"devices": []}


def test_the_launch_password_works_only_on_this_pc(tailnet_api):
    assert tailnet_api.client.get("/api/devices", headers={**VIA_TAILNET, **bearer(PASSWORD)}).status_code == 401


@pytest.mark.parametrize("forwarded", [{"X-Forwarded-For": "100.101.102.103"},
                                       {"X-Forwarded-Host": "127.0.0.1:47615"}])
def test_the_launch_password_never_counts_through_tailscale_serve(api, forwarded):
    # tailscale serve passes a client's Host through unchanged, and always sets these two
    assert api.client.get("/api/devices", headers={**bearer(PASSWORD), **forwarded}).status_code == 401


def test_a_paired_device_cant_manage_devices(tailnet_api):
    _, key = tailnet_api.pair()
    r = tailnet_api.client.get("/api/devices", headers={**VIA_TAILNET, **bearer(key)})
    assert r.status_code == 403


def test_revoked_keys_and_wrong_keys_are_the_same_401(tailnet_api):
    device_id, key = tailnet_api.pair()
    assert tailnet_api.client.delete(f"/api/devices/{device_id}", headers=bearer(PASSWORD)).status_code == 200
    r = tailnet_api.client.get("/api/devices", headers={**VIA_TAILNET, **bearer(key)})
    assert r.status_code == 401
    assert tailnet_api.client.delete("/api/devices/99", headers=bearer(PASSWORD)).status_code == 404


@pytest.mark.parametrize("login, status", [
    ("x@y.com", 403), ("me@gmail.com", 200), ("ME@Gmail.com", 200),
    ("=?utf-8?q?me=40gmail=2Ecom?=", 200), ("=?utf-8?q?x=40y=2Ecom?=", 403),  # how Tailscale encodes non-ASCII
])
def test_a_tailscale_login_header_must_be_the_owners(tailnet_api, login, status):
    # On a local request, the header is what a forging local process would send
    h = {**bearer(PASSWORD), "Tailscale-User-Login": login}
    assert tailnet_api.client.get("/api/devices", headers=h).status_code == status


def test_no_login_setting_means_no_login_check(api):
    h = {**bearer(PASSWORD), "Tailscale-User-Login": "anyone@example.com"}
    assert api.client.get("/api/devices", headers=h).status_code == 200


def test_pairing_checks_the_tailscale_login_too(tailnet_api):  # M2 review: a stranger can't burn the code
    code = new_pairing_code(tailnet_api.store, NOW)
    stranger = {**VIA_TAILNET, "Tailscale-User-Login": "stranger@example.com"}
    for _ in range(5):
        assert tailnet_api.client.post("/api/pair", json={"code": "000000", "device_name": "x"},
                                       headers=stranger).status_code == 403
    owner = {**VIA_TAILNET, "Tailscale-User-Login": "me@gmail.com"}
    assert tailnet_api.client.post("/api/pair", json={"code": code, "device_name": "laptop"},
                                   headers=owner).status_code == 200


def test_pairing_over_the_api_returns_a_working_key(api):
    code = new_pairing_code(api.store, NOW)
    r = api.client.post("/api/pair", json={"code": code, "device_name": "laptop"})
    assert r.status_code == 200 and device_for_key(api.store, r.json()["key"], NOW) == r.json()["device_id"]
    assert [d["name"] for d in api.get("/api/devices")["devices"]] == ["laptop"]


def test_a_wrong_pairing_code_is_a_400_with_no_detail_about_the_code(api):
    new_pairing_code(api.store, NOW)
    r = api.client.post("/api/pair", json={"code": "12345x", "device_name": "laptop"})
    assert r.status_code == 400 and "12345x" not in r.text


def test_an_overlong_pairing_code_is_a_422(api):
    assert api.client.post("/api/pair", json={"code": "1" * 100, "device_name": "x"}).status_code == 422


def test_an_oversized_body_is_refused_unread(api):  # M2 review: FastAPI would read it whole and echo it in a 422
    from assistant.engine.api import MAX_BODY

    big = b"x" * (MAX_BODY + 1)
    for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "application/json"}):
        r = api.client.post("/api/pair", content=big, headers=headers)
        assert r.status_code == 413 and len(r.content) < 100
    chunked = api.client.post("/api/pair", content=iter([b"x" * 3000] * 2))  # no Content-Length
    assert chunked.status_code == 413
    assert api.client.post("/api/pair", content=b"{}", headers={"Content-Length": "-1"}).status_code in (400, 413)


def test_a_small_body_still_reaches_the_route(api):
    code = new_pairing_code(api.store, NOW)
    r = api.client.post("/api/pair", content=json.dumps({"code": code, "device_name": "laptop"}).encode(),
                        headers={"Content-Type": "application/json"})
    assert r.status_code == 200


def preflight(client, origin):
    return client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})


def test_cors_allows_only_the_tauri_origin(api):
    assert preflight(api.client, "http://tauri.localhost").status_code == 200
    assert preflight(api.client, "http://localhost:5173").status_code == 400
    assert preflight(api.client, "https://evil.example.com").status_code == 400
    assert "access-control-allow-origin" not in api.client.get(
        "/api/health", headers={"Origin": "https://evil.example.com"}).headers


def test_dev_mode_also_allows_the_vite_origin(tmp_path):
    dev = Api(tmp_path, dev=True)
    assert preflight(dev.client, "http://localhost:5173").status_code == 200
    dev.close()


def test_there_is_no_public_documentation_route(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert api.client.get(path).status_code == 404


def test_no_secret_reaches_the_log(tmp_path, caplog, monkeypatch):  # M2 review: every level, every logger
    import logging

    import httpx2

    from assistant.engine.server import ApiServer

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(logging.getLogger("assistant.engine"), "propagate", True)  # setup_logging turns it off
    port = free_port()
    made = Api(tmp_path, f"[api]\nport = {port}\n")
    server = ApiServer(made.app, port)
    server.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _, key = made.pair()
        code = new_pairing_code(made.store, NOW)
        httpx2.post(f"{base}/api/pair", json={"code": code, "device_name": "x"})
        httpx2.post(f"{base}/api/pair", json={"code": code, "device_name": "x"})  # used up: refused
        for secret in (PASSWORD, key, "wrong"):
            httpx2.get(f"{base}/api/devices", headers=bearer(secret))
    finally:
        server.stop()
        made.stop.set()
        made.close()
    assert caplog.records  # the capture works: uvicorn logs its start at INFO
    assert not any(s in caplog.text for s in (PASSWORD, key, code))
