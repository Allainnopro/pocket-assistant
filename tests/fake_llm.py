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
