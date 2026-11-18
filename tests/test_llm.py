"""The Anthropic adapter: request shape, reply parsing, client safety, error
mapping and the ledger round trip. No test reaches the network: the guard in
conftest.py raises NetworkBlocked on any non-local host.

Run: pytest
"""

import json
import math

import anthropic
import httpx2
import pytest
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from fake_llm import FIXTURES, FakeLLM, fixture
from pydantic import BaseModel

from assistant.costs import Caps, Usage, attempts_cost, attempts_from_usage, estimate_usd
from assistant.errors import CapReached, SecretError
from assistant.llm import (
    BASE_URL, AnthropicLLM, LlmNoAnswer, LlmRejected, LlmRequest, build_params, call_logged, reply_from_message,
)
from assistant.store import Store

from conftest import NetworkBlocked

FAKE_KEY = "sk-ant-api03-" + "Zx9_-" * 18 + "wxyz"  # the shape of a real key, not a real key


def request(**changes) -> LlmRequest:
    base = dict(purpose="extract", model="claude-haiku-5-5", system="You extract.",
                messages=[{"role": "user", "content": "hi"}], max_tokens=4_000,
                output_schema={"type": "object", "properties": {}, "additionalProperties": False},
                effort="low", thinking_off=True)
    return LlmRequest(**{**base, **changes})


def test_only_llm_py_imports_the_sdk():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "assistant"
    importers = sorted(p.name for p in src.glob("*.py")
                       if re.search(r"^\s*(import anthropic|from anthropic)", p.read_text(encoding="utf-8"), re.M))
    assert importers == ["llm.py"]


# --- fixtures are real API shapes ----------------------------------------------------

def unknown_keys(model: BaseModel, path: str = "") -> list[str]:
    """Keys the SDK didn't recognise anywhere in a parsed reply. The SDK accepts
    them silently, so a misspelled fixture would otherwise test nothing."""
    found = [f"{path}.{key}" for key in (model.model_extra or {})]
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            found += unknown_keys(value, f"{path}.{name}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, BaseModel):
                    found += unknown_keys(item, f"{path}.{name}[{i}]")
    return found


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*.json")))
def test_every_fixture_is_a_real_reply_shape(name):
    data = fixture(name)
    parsed = (BetaMessage if "beta" in name else Message).model_validate(data)
    assert data["usage"]["input_tokens"] > 0 and data["model"] and data["stop_reason"]
    if name.startswith("synthetic_"):
        # Hand-written: a misspelled key would silently test nothing. (Recorded replies
        # can carry fields newer than the SDK's types: the real one has usage.speed.)
        assert unknown_keys(parsed) == []


def test_the_recorded_live_reply_reads_and_prices_as_expected():
    # From the live smoke call of 2026-11-18, re-recorded for the email's kind and urgency (owner approved):
    # invented email and candidate
    reply = read("recorded_smoke_ok.json")
    assert reply.attempts == [("claude-haiku-5-5", Usage(477, 451, cache_write_5m_tokens=3_798))]
    # 477 x $0.10 + 451 x $0.50 + 3,798 x $0.125 (5-minute cache write) = $0.00074795, as the API billed
    assert attempts_cost(reply.attempts) == pytest.approx(0.00074795)
    from assistant.extraction import parse_extraction

    extraction = parse_extraction(reply.text)
    assert [(i.kind, i.location_scope) for i in extraction.items] == [("hackathon", "hcmc"),
                                                                     ("competition", "remote_international")]
    # recorded two days before the invented hackathon's 2026-11-20 deadline: registering is due, and urgent
    assert extraction.needs_action is True and "2026-11-20" in extraction.action_note
    assert (extraction.email_kind, extraction.urgent) == ("hackathon", True)


def test_the_misspelling_check_itself_works():
    data = fixture("synthetic_ok.json")
    data["stop_reasn"] = data.pop("stop_reason")
    data["usage"]["cache_read_tokens"] = 5  # our name, not the API's
    assert unknown_keys(Message.model_validate(data)) == [".stop_reasn", ".usage.cache_read_tokens"]


# --- reading replies -------------------------------------------------------------------

def read(name, model="claude-haiku-5-5"):
    return reply_from_message(fixture(name), requested_model=model, request_id="req_1")


def test_an_ok_reply_gives_its_text_and_what_it_billed():
    reply = read("synthetic_ok.json")
    assert json.loads(reply.text)["items"][0]["kind"] == "hackathon"
    assert reply.attempts == [("claude-haiku-5-5", Usage(412, 296, cache_write_5m_tokens=1_650))]
    # 412 x $0.10 + 296 x $0.50 + 1,650 x $0.125 (5-minute write) = $0.00039545
    assert attempts_cost(reply.attempts) == pytest.approx(0.00039545)


def test_only_text_blocks_are_joined_and_thinking_is_counted():
    reply = read("synthetic_thinking_then_text.json")
    assert json.loads(reply.text) == {"items": [], "instructions_to_ai_detected": False, "needs_action": False,
                                      "action_note": None, "email_kind": "promo", "urgent": False}
    assert reply.attempts[0][1].thinking_tokens == 150


def test_a_refusal_keeps_its_category_and_nulls_count_as_zero():
    reply = read("synthetic_refusal.json")
    assert (reply.stop_reason, reply.refusal_category, reply.text) == ("refusal", "cyber", "")
    assert reply.attempts == [("claude-haiku-5-5", Usage(2_062, 0))]


def test_a_cut_off_reply_says_so():
    reply = read("synthetic_max_tokens.json")
    assert reply.stop_reason == "max_tokens"
    assert reply.attempts[0][1] == Usage(2_062, 4_000, cache_read_tokens=1_650)


def test_a_fallback_bills_each_attempt_at_its_own_model():
    reply = read("synthetic_beta_fallback.json", model="claude-opus-5-5")
    assert reply.model == "claude-opus-5"
    assert reply.attempts == [("claude-opus-5-5", Usage(4_000, 0)),
                              ("claude-opus-5", Usage(4_000, 700, thinking_tokens=300))]
    # 4,000 x $4 (declined) + 4,000 x $5 + 700 x $25 = $0.0535; top-level usage alone would give $0.0375
    assert attempts_cost(reply.attempts) == pytest.approx(0.0535)


@pytest.mark.parametrize("usage, expected", [
    # a write whose time-to-live isn't reported is priced as the dearer 1-hour write
    ({"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 300},
     Usage(10, 5, cache_write_1h_tokens=300)),
    ({"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 300,
      "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 50}},
     Usage(10, 5, cache_write_5m_tokens=200, cache_write_1h_tokens=100)),
    # thinking is part of output, never more than it
    ({"input_tokens": 10, "output_tokens": 5, "output_tokens_details": {"thinking_tokens": 9}},
     Usage(10, 5, thinking_tokens=5)),
])
def test_usage_mapping_fails_expensive(usage, expected):
    assert attempts_from_usage(usage, served_model="m", requested_model="m") == [("m", expected)]


def test_an_iteration_without_a_model_is_the_requested_model():
    usage = {"input_tokens": 1, "output_tokens": 1,
             "iterations": [{"type": "message", "input_tokens": 7, "output_tokens": 3}]}
    assert attempts_from_usage(usage, served_model="served", requested_model="asked") == [("asked", Usage(7, 3))]


# --- the request we send ------------------------------------------------------------------

def test_the_request_caches_the_system_prompt_and_asks_for_json():
    params = build_params(request(tools=[{"name": "read_email"}]))
    assert params["system"] == [{"type": "text", "text": "You extract.", "cache_control": {"type": "ephemeral"}}]
    assert params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["thinking"] == {"type": "disabled"}
    assert params["tool_choice"] == {"type": "none"}  # tools only frame the email
    assert "temperature" not in params  # Haiku 5.5 rejects sampling parameters


def test_thinking_stays_on_unless_asked_and_cant_be_off_at_high_effort():
    assert "thinking" not in build_params(request(thinking_off=False))
    with pytest.raises(ValueError):
        build_params(request(effort="xhigh"))


def test_the_prompt_bound_is_at_least_the_bytes_sent():
    vietnamese = request(messages=[{"role": "user", "content": "Học bổng " * 500}])
    assert vietnamese.prompt_bound() > len(("Học bổng " * 500).encode("utf-8"))


# --- the client: the key and the destination can't be redirected ---------------------------

@pytest.fixture
def hostile_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-from-the-environment")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-from-the-environment")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Evil: 1")
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")


def test_environment_variables_cant_redirect_or_reconfigure_the_client(hostile_env):
    import os

    client = AnthropicLLM(FAKE_KEY)._client
    assert str(client.base_url).rstrip("/") == BASE_URL
    assert (client.api_key, client.auth_token, client.max_retries) == (FAKE_KEY, None, 0)
    assert "X-Evil" not in client.default_headers
    assert os.environ["ANTHROPIC_CUSTOM_HEADERS"] == "X-Evil: 1"  # put back for everyone else


def test_a_real_request_goes_to_api_anthropic_com_only(hostile_env):
    with pytest.raises(NetworkBlocked, match=r"'api\.anthropic\.com'"):
        AnthropicLLM(FAKE_KEY).complete(request())


def test_proxies_and_certificate_overrides_are_ignored(hostile_env, monkeypatch, tmp_path):
    # A leftover debugging proxy plus its CA would see the key and every email in clear text
    monkeypatch.delenv("NO_PROXY", raising=False)
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://proxy.evil.example:8080")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "proxy-ca.pem"))  # missing: reading it would crash
    with pytest.raises(NetworkBlocked, match=r"'api\.anthropic\.com'"):  # not 'proxy.evil.example'
        AnthropicLLM(FAKE_KEY).complete(request())


def test_sdk_debug_logging_and_tracing_stay_off():
    import logging

    for name in ("anthropic", "httpx2"):
        logging.getLogger(name).setLevel(logging.DEBUG)  # what ANTHROPIC_LOG=debug does at import
    llm = AnthropicLLM(FAKE_KEY)
    for name in ("anthropic", "httpx2"):  # at DEBUG the SDK logs whole requests, email text included
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    assert llm._client._tracer is None  # no spans that could copy request content


def test_the_key_comes_from_credential_manager(vault):
    with pytest.raises(SecretError, match="set-key anthropic"):
        AnthropicLLM.from_vault()
    vault[("pocket-assistant", "anthropic-api-key")] = FAKE_KEY
    assert AnthropicLLM.from_vault()._client.api_key == FAKE_KEY


# --- errors -------------------------------------------------------------------------------------

API_REQUEST = httpx2.Request("POST", f"{BASE_URL}/v1/messages")


def status_error(cls, status, message="error"):
    response = httpx2.Response(status, request=API_REQUEST, headers={"request-id": "req_err"})
    return cls(message, response=response, body=None)


class RaisingClient:
    def __init__(self, exc):
        self.messages = self
        self.exc = exc

    def create(self, **kwargs):
        raise self.exc


def complete_raising(exc):
    llm = AnthropicLLM(FAKE_KEY)
    llm._client = RaisingClient(exc)
    return llm.complete(request())


def test_a_timeout_or_lost_connection_may_have_been_billed():
    for exc in (anthropic.APITimeoutError(request=API_REQUEST), anthropic.APIConnectionError(request=API_REQUEST)):
        with pytest.raises(LlmNoAnswer) as caught:
            complete_raising(exc)
        assert caught.value.kind == type(exc).__name__


@pytest.mark.parametrize("cls, status, retryable", [
    (anthropic.RateLimitError, 429, True),
    (anthropic.OverloadedError, 529, True),
    (anthropic.InternalServerError, 500, True),
    (anthropic.BadRequestError, 400, False),
    (anthropic.NotFoundError, 404, False),
])
def test_an_error_response_is_classified(cls, status, retryable):
    with pytest.raises(LlmRejected) as caught:
        complete_raising(status_error(cls, status))
    assert (caught.value.status, caught.value.retryable, caught.value.request_id) == (status, retryable, "req_err")


@pytest.mark.parametrize("cls, status, message, hint", [
    # the errors doc: a spend limit set in the Console comes back as a 400, not a 429
    (anthropic.BadRequestError, 400, "You have reached your specified workspace API usage limits.",
     "spend limit set in the Console"),
    (anthropic.APIStatusError, 402, "Your credit balance is too low.", "Out of API credit"),
    (anthropic.APIStatusError, 408, "Request timed out.", "busy or had a problem"),
    (anthropic.BadRequestError, 400, "messages.0.content: Field required", "bug or a config problem"),
])
def test_an_error_names_its_likely_cause(cls, status, message, hint):
    with pytest.raises(LlmRejected, match=hint):
        complete_raising(status_error(cls, status, message))


def test_a_key_cut_off_by_the_length_limit_is_still_hidden():
    with pytest.raises(LlmRejected) as caught:
        complete_raising(status_error(anthropic.BadRequestError, 400, "x" * 285 + FAKE_KEY))
    assert "sk-ant-api03" not in str(caught.value) and "Zx9" not in str(caught.value)


def test_an_auth_error_explains_the_fix_and_never_shows_the_key():
    with pytest.raises(LlmRejected) as caught:
        complete_raising(status_error(anthropic.AuthenticationError, 401, f"invalid x-api-key {FAKE_KEY}"))
    message = str(caught.value)
    assert "set-key anthropic" in message and FAKE_KEY not in message and "wxyz" not in message


# --- the ledger round trip ------------------------------------------------------------------------

NOW = __import__("datetime").datetime(2026, 10, 16, 3, tzinfo=__import__("datetime").timezone.utc)


def run(store, llm, req=None, parse=json.loads):
    return call_logged(store, llm, req or request(), parse, caps=Caps(5.0, 1.0), now_utc=NOW)


def ledger(store):
    return [tuple(r) for r in store.conn.execute(
        "SELECT purpose, outcome, cost_usd, model_served, request_id, error_class FROM llm_calls ORDER BY id")]


def test_an_ok_call_is_settled_at_its_real_cost(tmp_path):
    store, llm = Store(tmp_path / "a.db"), FakeLLM(fixture("synthetic_ok.json"))
    result = run(store, llm)
    assert result.outcome == "ok" and len(result.value["items"]) == 2
    assert ledger(store) == [("extract", "ok", pytest.approx(0.00039545), "claude-haiku-5-5", "req_fake", None)]
    assert len(llm.requests) == 1


@pytest.mark.parametrize("name, outcome", [
    ("synthetic_refusal.json", "refused"), ("synthetic_max_tokens.json", "max_tokens"),
])
def test_a_refusal_or_cut_off_reply_is_settled_as_such(tmp_path, name, outcome):
    store = Store(tmp_path / "a.db")
    assert run(store, FakeLLM(fixture(name))).outcome == outcome
    assert ledger(store)[0][1] == outcome


def test_json_that_doesnt_fit_is_settled_invalid_and_still_paid_for(tmp_path):
    store = Store(tmp_path / "a.db")
    reply = fixture("synthetic_ok.json")
    reply["content"][0]["text"] = "{not json"
    result = run(store, FakeLLM(reply))
    assert result.outcome == "invalid" and result.value is None
    assert ledger(store)[0][1:3] == ("invalid", pytest.approx(0.00039545))
    assert ledger(store)[0][5] == "JSONDecodeError"


def test_an_error_response_costs_nothing_and_is_raised(tmp_path):
    store = Store(tmp_path / "a.db")
    rejected = LlmRejected("busy", kind="OverloadedError", status=529, request_id="req_err")
    with pytest.raises(LlmRejected):
        run(store, FakeLLM(rejected))
    assert ledger(store) == [("extract", "api_error", 0.0, None, "req_err", "OverloadedError")]


def test_a_timeout_keeps_the_worst_case_estimate(tmp_path):
    store, req = Store(tmp_path / "a.db"), request()
    with pytest.raises(LlmNoAnswer):
        run(store, FakeLLM(LlmNoAnswer("timed out", kind="APITimeoutError")), req)
    worst = estimate_usd(req.model, req.prompt_bound(), req.max_tokens)
    assert ledger(store) == [("extract", "unknown", pytest.approx(worst), None, None, "LlmNoAnswer")]
    assert not math.isclose(worst, 0)


def test_a_capped_call_never_reaches_the_model(tmp_path):
    store, llm = Store(tmp_path / "a.db"), FakeLLM(fixture("synthetic_ok.json"))
    with pytest.raises(CapReached):
        call_logged(store, llm, request(), json.loads, caps=Caps(5.0, 0.0001), now_utc=NOW)
    assert llm.requests == []



@pytest.mark.parametrize("cause", [httpx2.ConnectError("no route to host"), httpx2.ConnectTimeout("connect"),
                                   httpx2.PoolTimeout("pool")])
def test_a_call_that_never_left_this_pc_is_unreachable_not_unanswered(cause):
    from assistant.llm import LlmUnreachable

    for cls in (anthropic.APIConnectionError, anthropic.APITimeoutError):
        exc = cls(request=API_REQUEST)
        exc.__cause__ = cause  # what the SDK raises `from`
        with pytest.raises(LlmUnreachable):
            complete_raising(exc)


def test_a_read_timeout_may_have_been_billed():
    from assistant.llm import LlmUnreachable

    exc = anthropic.APITimeoutError(request=API_REQUEST)
    exc.__cause__ = httpx2.ReadTimeout("read")
    with pytest.raises(LlmNoAnswer) as caught:
        complete_raising(exc)
    assert not isinstance(caught.value, LlmUnreachable)


def test_an_unreachable_api_costs_nothing(tmp_path):
    from assistant.llm import LlmUnreachable

    store = Store(tmp_path / "a.db")
    with pytest.raises(LlmUnreachable):
        run(store, FakeLLM(LlmUnreachable("no connection", kind="APIConnectionError")))
    assert ledger(store) == [("extract", "api_error", 0.0, None, None, "APIConnectionError")]



def test_the_vault_llm_rebuilds_only_when_the_saved_key_changes():
    from assistant.llm import VaultLLM

    keys, built = ["key-1"], []

    def build(key):
        built.append(key)
        return FakeLLM(fixture("synthetic_ok.json"), fixture("synthetic_ok.json"))

    llm = VaultLLM(load=lambda: keys[0], build=build)
    assert llm.refresh() is False and built == ["key-1"]
    keys[0] = "key-2"
    assert llm.refresh() is True and built == ["key-1", "key-2"]
    assert llm.complete(request()).stop_reason == "end_turn"
