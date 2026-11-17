"""The API's contract, committed for the dashboard: app/src/api/openapi.json, from which
`npm run gen:api` makes the TypeScript types.

Run: pytest. After changing a route: `assistant api-schema`, then `npm run gen:api` in app/.
"""

import json
from pathlib import Path

from api_helpers import dummy_ctx

from assistant import cli
from assistant.engine.api import create_app

ROOT = Path(__file__).parent.parent
COMMITTED = ROOT / "app" / "src" / "api" / "openapi.json"

SPEC_ROUTES = {  # spec section 5, without POST /api/settings/telegram/pair (it arrives with M6)
    ("get", "/api/health"), ("get", "/api/today"),
    ("get", "/api/opportunities"), ("get", "/api/opportunities/{opportunity_id}"),
    ("post", "/api/opportunities/{opportunity_id}/{action}"),
    ("get", "/api/emails"), ("get", "/api/emails/{message_id}"),
    ("get", "/api/reports/spend"), ("get", "/api/reports/digests"), ("get", "/api/reports/items"),
    ("get", "/api/search"), ("get", "/api/settings"), ("post", "/api/settings/rescore"),
    ("get", "/api/events"), ("post", "/api/pair"),
    ("get", "/api/devices"), ("delete", "/api/devices/{device_id}"),
}


def test_the_committed_openapi_document_matches_the_api():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == create_app(dummy_ctx()).openapi(), "run `assistant api-schema`, then `npm run gen:api` in app/"


def test_every_route_in_the_spec_is_in_the_document():
    paths = json.loads(COMMITTED.read_text(encoding="utf-8"))["paths"]
    assert {(method, path) for path, ops in paths.items() for method in ops} == SPEC_ROUTES


def test_api_schema_writes_the_document_without_a_config(tmp_path, monkeypatch, capsys):
    out = tmp_path / "app" / "src" / "api" / "openapi.json"
    monkeypatch.setattr(cli, "API_SCHEMA", out)
    monkeypatch.chdir(tmp_path)  # no config.toml here: the contract is the same on every PC
    cli.main(["api-schema"])
    written = out.read_bytes()
    assert b"\r" not in written and written.endswith(b"}\n")
    assert json.loads(written) == create_app(dummy_ctx()).openapi()
    assert str(out) in capsys.readouterr().out
