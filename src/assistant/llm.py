"""The one door to the Anthropic API. This is the only module that imports `anthropic`.

Everything else talks to the small LLM interface below, so tests use a
FakeLLM. (SDK 1.x sends requests through httpx2, which HTTP-mocking tools
such as respx can't see, so faking at this seam is also the reliable way.)

Security decisions, explained:
- The key comes from Windows Credential Manager (vault.py), never from
  ANTHROPIC_API_KEY. Passing it explicitly also stops the SDK reading any
  other credential source: auth tokens, profiles, federation.
- base_url is explicit, so a stray ANTHROPIC_BASE_URL can't send the key
  and your email to another server. Proxy settings (HTTPS_PROXY, the Windows
  proxy) and certificate overrides (SSL_CERT_FILE) are ignored too: a
  leftover debugging proxy that your PC trusts could otherwise read the key
  and every email in clear text. The connection goes straight to Anthropic,
  checked against Windows' own certificate store.
- ANTHROPIC_CUSTOM_HEADERS is ignored, OpenTelemetry tracing (which can
  copy request content) is off, and the SDK's debug logging (which prints
  whole requests) stays silenced.
- max_retries=0: one ledger reservation covers exactly one HTTP request.
  Retrying is a decision for the caller, made after the ledger is updated.

Billing, from Anthropic's billing help: an HTTP error response is not
charged; a request that times out on our side may still be charged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypeVar

from .costs import Caps, Usage, attempts_from_usage, estimate_usd
from .errors import AssistantError
from .vault import load, redact

BASE_URL = "https://api.anthropic.com"
IGNORED_ENV = ("ANTHROPIC_CUSTOM_HEADERS",)  # the SDK would add these headers even to an explicit client
DIRECT_ROUTES = ("all://", "http://", "https://")  # every proxy route the environment can set
# Tokens the API adds around what we send (role markers, tool-use instructions).
# Generous on purpose: see LlmRequest.prompt_bound.
PROMPT_OVERHEAD_TOKENS = 2_000

T = TypeVar("T")


@dataclass(frozen=True)
class LlmRequest:
    purpose: str                       # costs.PURPOSES
    model: str
    system: str                        # identical on every call, so it is cached
    messages: list[dict[str, Any]]
    max_tokens: int
    output_schema: dict[str, Any]      # JSON schema, already through transform_schema
    effort: str = "low"
    thinking_off: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    timeout_s: float = 120.0

    def prompt_bound(self) -> int:
        """An upper bound on the prompt's tokens, for the worst-case estimate.

        A token is at least one byte of UTF-8, so the byte length of what we
        send is an upper bound, plus a margin for the API's own framing.
        Vietnamese text is 2-3 bytes a letter, so this over-reserves a lot,
        but only until the call settles; it never under-reserves.
        """
        sent = json.dumps([self.system, self.messages, self.tools, self.output_schema], ensure_ascii=False)
        return len(sent.encode("utf-8")) + PROMPT_OVERHEAD_TOKENS


@dataclass(frozen=True)
class LlmReply:
    text: str                          # the text blocks joined; thinking and other blocks left out
    stop_reason: str | None
    refusal_category: str | None
    model: str                         # the model that answered
    request_id: str | None
    attempts: list[tuple[str, Usage]]  # what was billed, per attempt
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # the whole Message, for recording fixtures


class LlmError(AssistantError):
    """A model call that didn't produce a reply."""

    def __init__(self, message: str, *, kind: str, request_id: str | None = None) -> None:
        super().__init__(redact(message))
        self.kind = kind               # an exception class name, safe to store
        self.request_id = request_id


class LlmRejected(LlmError):
    """The API answered with an error status. Not billed."""

    def __init__(self, message: str, *, kind: str, status: int, request_id: str | None = None) -> None:
        super().__init__(message, kind=kind, request_id=request_id)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Busy or broken on Anthropic's side; anything else 4xx is our request or our key."""
        return self.status in (408, 409, 429) or self.status >= 500


class LlmNoAnswer(LlmError):
    """Timeout or lost connection: the server may have finished, and billed, the call."""


class LlmUnreachable(LlmError):
    """The request never left this PC (no network, DNS, a connection that never
    opened): nothing was sent, so nothing was billed."""


class LLM(Protocol):
    def complete(self, request: LlmRequest) -> LlmReply: ...


def build_params(request: LlmRequest) -> dict[str, Any]:
    """The messages.create arguments. Pure, so tests check the exact shape we send."""
    if request.thinking_off and request.effort in ("xhigh", "max"):
        raise ValueError("thinking can only be turned off at effort high or below")
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
        "messages": request.messages,
        "output_config": {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": request.output_schema},
        },
    }
    if request.thinking_off:
        params["thinking"] = {"type": "disabled"}
    if request.tools:
        params["tools"] = request.tools
        params["tool_choice"] = {"type": "none"}  # tools only frame the email; the model never calls them
    return params


def reply_from_message(message: Mapping[str, Any], *, requested_model: str, request_id: str | None) -> LlmReply:
    """A Message (as a dict: message.to_dict()) -> LlmReply. Pure: fixtures test it."""
    blocks = message.get("content") or []
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    served = message.get("model") or requested_model
    return LlmReply(
        text=text,
        stop_reason=message.get("stop_reason"),
        refusal_category=(message.get("stop_details") or {}).get("category"),
        model=served,
        request_id=request_id,
        attempts=attempts_from_usage(message.get("usage") or {}, served_model=served,
                                     requested_model=requested_model),
        raw=dict(message),
    )


@contextmanager
def _ignoring_env(names: tuple[str, ...]) -> Iterator[None]:
    saved = {name: os.environ.pop(name) for name in names if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


class AnthropicLLM:
    def __init__(self, api_key: str) -> None:
        import anthropic

        for name in ("anthropic", "httpx2"):
            # ANTHROPIC_LOG=debug would log every request, email text included
            logging.getLogger(name).setLevel(logging.WARNING)
        # trust_env=False: no SSL_CERT_FILE/SSL_CERT_DIR. The SDK still adds
        # proxy routes from the environment, so mount "no proxy" over them.
        http_client = anthropic.DefaultHttpxClient(trust_env=False, mounts=dict.fromkeys(DIRECT_ROUTES))
        with _ignoring_env(IGNORED_ENV):
            self._client = anthropic.Anthropic(
                api_key=api_key, base_url=BASE_URL, max_retries=0, open_telemetry=False, http_client=http_client,
            )
        self._anthropic = anthropic

    @classmethod
    def from_vault(cls) -> AnthropicLLM:
        return cls(load("anthropic"))

    def complete(self, request: LlmRequest) -> LlmReply:
        sdk = self._anthropic
        try:
            message = self._client.messages.create(**build_params(request), timeout=request.timeout_s)
        except sdk.APIStatusError as exc:
            raise LlmRejected(_explain(exc), kind=type(exc).__name__, status=exc.status_code,
                              request_id=exc.request_id) from None
        except sdk.APIConnectionError as exc:  # APITimeoutError included
            import httpx2  # the SDK's HTTP library; max_retries=0, so the cause is this one try

            if isinstance(exc.__cause__, (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)):
                raise LlmUnreachable(f"Can't reach the Anthropic API ({type(exc.__cause__).__name__}); "
                                     "nothing was sent.", kind=type(exc).__name__) from None
            raise LlmNoAnswer(f"No answer from the Anthropic API ({type(exc).__name__}); it may still have been "
                              "billed, so the ledger keeps its worst-case estimate.",
                              kind=type(exc).__name__) from None
        return reply_from_message(message.to_dict(), requested_model=request.model,
                                  request_id=getattr(message, "_request_id", None))


class VaultLLM:
    """AnthropicLLM with the key `assistant set-key` saved, built again when that key
    changes, so a running engine picks up a new key without a restart. Only a
    fingerprint of the key is kept here; the client holds the key itself."""

    def __init__(self, load: Callable[[], str] = lambda: load("anthropic"),
                 build: Callable[[str], LLM] = AnthropicLLM) -> None:
        self._load, self._build = load, build
        key = load()
        self._llm, self._fingerprint = build(key), _fingerprint(key)

    def refresh(self) -> bool:
        """Read the saved key again; True when it changed and the client was rebuilt."""
        key = self._load()
        if _fingerprint(key) == self._fingerprint:
            return False
        self._llm, self._fingerprint = self._build(key), _fingerprint(key)
        return True

    def complete(self, request: LlmRequest) -> LlmReply:
        return self._llm.complete(request)


def _fingerprint(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def _explain(exc: Any) -> str:
    """A user-facing message for an error response, from the errors doc. The API's
    own text is kept (redacted, then shortened): it says what was wrong."""
    status, kind = exc.status_code, getattr(exc, "type", None)
    detail = redact(str(getattr(exc, "message", "") or ""))[:300]
    said = detail.lower()
    if status == 400 and "limit" in said and ("spend" in said or "usage" in said):
        # The docs: a spend limit you set in the Console comes back as a 400, not a 429
        hint = ("A spend limit set in the Console was reached (the pocket-assistant workspace's monthly "
                "limit, or the organization's). Calls work again when it resets or you raise it.")
    elif status == 401:
        hint = "The API key was rejected. Create a key in the pocket-assistant workspace and run: " \
               "assistant set-key anthropic"
    elif status == 402 or kind == "billing_error":
        hint = "Out of API credit. Buy credits in the Console: Settings -> Billing."
    elif status == 403:
        hint = "This key may not use that model or workspace, or the account isn't allowed here."
    elif status == 404:
        hint = "Unknown model or endpoint. Check [llm] in config.toml."
    elif status == 413:
        hint = "The request was too large."
    elif status == 429:
        hint = "Rate limited. The next run tries again."
    elif status in (408, 409) or status >= 500:
        hint = "Anthropic was busy or had a problem. The next run tries again."
    else:
        hint = "The request was refused; this is a bug or a config problem."
    return f"Anthropic API error {status} ({type(exc).__name__}): {hint} Details: {detail}"


@dataclass(frozen=True)
class CallResult:
    outcome: str                       # ok | invalid | refused | max_tokens
    value: Any                         # what parse() returned, when outcome is "ok"
    reply: LlmReply
    problem: str | None = None         # why the reply was invalid, shortened and redacted


def call_logged(
    store, llm: LLM, request: LlmRequest, parse: Callable[[str], T], *,
    caps: Caps, now_utc: datetime, message_id: str | None = None,
) -> CallResult:
    """Reserve at the worst case, call, settle with what came back.

    Raises CapReached before any call when a cap blocks it, LlmRejected and
    LlmUnreachable (settled 'api_error', $0) and LlmNoAnswer (settled 'unknown',
    estimate kept). parse() raising ValueError makes the outcome 'invalid'.
    """
    estimate = estimate_usd(request.model, request.prompt_bound(), request.max_tokens)
    with store.llm_call(now_utc=now_utc, purpose=request.purpose, model=request.model,
                        estimate_usd=estimate, caps=caps, message_id=message_id) as call:
        try:
            reply = llm.complete(request)
        except LlmRejected as exc:
            call.settle("api_error", error_class=exc.kind, request_id=exc.request_id)
            raise
        except LlmUnreachable as exc:  # nothing left this PC: nothing to bill
            call.settle("api_error", error_class=exc.kind)
            raise
        # LlmNoAnswer or anything else unexpected: llm_call records 'unknown' and re-raises
        value, error_class, problem = None, None, None
        if reply.stop_reason == "refusal":
            # Billed at its input tokens even in the categories Anthropic doesn't
            # charge for (cyber, general_harms, none): the reply doesn't say which
            # attempt declined before output, so the ledger errs high.
            outcome = "refused"
        elif reply.stop_reason == "max_tokens":
            outcome = "max_tokens"
        else:
            try:
                value, outcome = parse(reply.text), "ok"
            except ValueError as exc:
                outcome, error_class = "invalid", type(exc).__name__
                problem = redact(str(exc))[:300]
        call.settle(outcome, reply.attempts, request_id=reply.request_id, stop_reason=reply.stop_reason,
                    refusal_category=reply.refusal_category, error_class=error_class)
    return CallResult(outcome, value, reply, problem)


def output_schema(model_cls) -> dict[str, Any]:
    """A pydantic model -> the JSON schema structured outputs accept (via the SDK's
    transform_schema, kept here so no other module imports anthropic). Constraints the
    API can't enforce become description text; code still validates the reply."""
    from anthropic import transform_schema

    return transform_schema(model_cls)


