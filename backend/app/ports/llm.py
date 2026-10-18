"""LLM port. Every model call in the system goes through this interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    task: str
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = 1500
    json_mode: bool = False
    # Opaque hints the deterministic mock uses to pick a canned response.
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class LLMClient(Protocol):
    provider: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
