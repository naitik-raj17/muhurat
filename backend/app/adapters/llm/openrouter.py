"""OpenRouter client (OpenAI-compatible chat completions API). Enabled by OPENROUTER_API_KEY."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.ports.llm import LLMRequest, LLMResponse


class OpenRouterLLM:
    provider = "openrouter"

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": "Wedding AI Operating Layer",
        }
        self._timeout = timeout

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions", json=body, headers=self._headers
            )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        return LLMResponse(
            text=data["choices"][0]["message"]["content"] or "",
            model=data.get("model", request.model),
            provider=self.provider,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
