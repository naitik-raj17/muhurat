"""Deterministic mock LLM. The default provider; used by every test.

It never calls a network. Responses are derived from the request so results are stable:
* `metadata["mock_response"]` (if present) is returned verbatim (tests use this to inject
  malformed output and exercise the retry / fallback path);
* task-specific handlers registered with `register_handler` produce structured JSON;
* otherwise a short deterministic acknowledgement is returned.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.ports.llm import LLMRequest, LLMResponse

Handler = Callable[[LLMRequest], str]
_HANDLERS: dict[str, Handler] = {}


def register_handler(task: str, handler: Handler) -> None:
    _HANDLERS[task] = handler


def estimate_tokens(text: str) -> int:
    # ~4 characters per token is the usual rule of thumb; good enough for budgets in mock mode.
    return max(1, len(text) // 4)


class MockLLM:
    provider = "mock"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        self.calls.append(request)
        if "mock_response" in request.metadata:
            text = request.metadata["mock_response"]
        elif request.task in _HANDLERS:
            text = _HANDLERS[request.task](request)
        else:
            last_user = next(
                (m.content for m in reversed(request.messages) if m.role == "user"), ""
            )
            text = f"[mock:{request.task}] {last_user[:200]}"
        prompt_text = "".join(m.content for m in request.messages)
        return LLMResponse(
            text=text,
            model=request.model,
            provider=self.provider,
            tokens_in=estimate_tokens(prompt_text),
            tokens_out=estimate_tokens(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
