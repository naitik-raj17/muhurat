"""Structured JSON logging with request IDs and PII masking."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.context import _request_id

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Phone-like runs of 8+ digits, optionally with +, spaces or dashes. A '-' on either side means
# the digits are part of a bigger token (e.g. a UUID like 48349086-36e2-...), not a phone.
_PHONE_RE = re.compile(r"(?<![\w-])(\+?\d[\d\s-]{6,}\d)(?![\w-])")


def mask_pii(text: str) -> str:
    """Mask emails (keep first char + domain) and phone numbers (keep last 2 digits)."""

    def _phone(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) < 8:
            return m.group(1)
        return "*" * (len(digits) - 2) + digits[-2:]

    text = _EMAIL_RE.sub(r"\1***@\2", text)
    return _PHONE_RE.sub(_phone, text)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_value(v) for v in value]
    return value


def _pii_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in list(event_dict.items()):
        if key in ("timestamp", "level", "request_id", "logger"):
            continue
        event_dict[key] = _mask_value(value)
    return event_dict


def _request_id_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    rid = _request_id.get()
    if rid and "request_id" not in event_dict:
        event_dict["request_id"] = rid
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _request_id_processor,
            structlog.processors.format_exc_info,
            _pii_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
