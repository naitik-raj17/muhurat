"""Deterministic responses for the mock LLM, per task.

They reuse the rule-based logic the system falls back to anyway, so mock mode behaves like a
model that always returns well-formed output. Tests inject malformed output through
`metadata["mock_response"]` to exercise validation, retry and fallback.
"""

from __future__ import annotations

import json
import re

from app.adapters.llm.mock import register_handler
from app.domain.contracts import heuristic_extract
from app.domain.guest_rules import classify_reply
from app.ports.llm import LLMRequest

_UNTRUSTED = re.compile(r"<untrusted-[a-z_]+>\n(.*)\n</untrusted-[a-z_]+>", re.DOTALL)


def _payload(request: LLMRequest) -> str:
    last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    m = _UNTRUSTED.search(last_user)
    return m.group(1) if m else last_user


def _contract(request: LLMRequest) -> str:
    return heuristic_extract(_payload(request)).model_dump_json()


def _guest_reply(request: LLMRequest) -> str:
    intent = classify_reply(_payload(request))
    if intent is None:
        return json.dumps({"status": "pending", "plus_ones": 0, "confidence": 0.2})
    return intent.model_dump_json()


def register_all() -> None:
    register_handler("contract_analysis", _contract)
    register_handler("guest_comm", _guest_reply)
