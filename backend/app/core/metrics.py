"""Prometheus metrics, exposed at /metrics.

Labels are kept low-cardinality: route templates (never raw paths with IDs), agent and tool
names, tiers and statuses.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)
AGENT_ACTIONS = Counter(
    "agent_actions_total", "Agent actions by outcome", ["agent", "tier", "status"]
)
OUTBOX_EVENTS = Counter("outbox_events_total", "Outbox events processed", ["event_type", "outcome"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["task", "model", "direction"])
LLM_COST_INR = Counter("llm_cost_inr_total", "Estimated LLM cost in INR", ["task", "model"])


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
