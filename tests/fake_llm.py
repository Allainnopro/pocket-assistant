"""A stand-in for AnthropicLLM: canned replies, every request recorded.

Replies are Message dicts (the fixtures in tests/fixtures/llm) or exceptions
to raise, used in order. Tests never call the real API.
"""

from __future__ import annotations

import json
from pathlib import Path

from assistant.llm import LlmReply, LlmRequest, reply_from_message

FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeLLM:
    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmReply:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply_from_message(reply, requested_model=request.model, request_id="req_fake")


class SameReplyLLM:
    """Thread-safe: the same reply on every call (after an optional different first
    one, which may be an exception), counting the calls."""

    def __init__(self, reply: dict, first=None) -> None:
        import threading

        self.reply, self.first = reply, first
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, request: LlmRequest) -> LlmReply:
        with self._lock:
            self.calls += 1
            answer = self.first if self.calls == 1 and self.first is not None else self.reply
        if isinstance(answer, BaseException):
            raise answer
        return reply_from_message(answer, requested_model=request.model, request_id="req_fake")
