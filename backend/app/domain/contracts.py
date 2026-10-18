"""Contract clause schema, quote verification, rule-based risk flags and rule-based extraction.

The LLM extracts clauses into `ContractExtraction`. Every clause must quote its source text,
and `verify_quotes` checks each quote really occurs in the document: a quote that doesn't is
flagged as a possible hallucination. `heuristic_extract` is the rule-based fallback used when
model output fails validation twice (and by the deterministic mock LLM).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.untrusted import find_suspicious

ClauseKey = Literal[
    "payment_schedule", "cancellation", "refund", "liability", "force_majeure", "deliverables"
]
CLAUSE_KEYS: tuple[ClauseKey, ...] = (
    "payment_schedule",
    "cancellation",
    "refund",
    "liability",
    "force_majeure",
    "deliverables",
)


class ExtractedClause(BaseModel):
    quote: str = Field(min_length=3, max_length=3000, description="Verbatim text from the contract")
    summary: str = Field(default="", max_length=500)


class ContractExtraction(BaseModel):
    """What the model must return. Missing clauses are null, never invented."""

    payment_schedule: ExtractedClause | None = None
    cancellation: ExtractedClause | None = None
    refund: ExtractedClause | None = None
    liability: ExtractedClause | None = None
    force_majeure: ExtractedClause | None = None
    deliverables: ExtractedClause | None = None
    advance_percent: float | None = Field(default=None, ge=0, le=100)
    advance_refundable: bool | None = None

    def clauses(self) -> dict[str, ExtractedClause]:
        return {k: c for k in CLAUSE_KEYS if (c := getattr(self, k)) is not None}


class RiskFlag(BaseModel):
    code: str
    severity: Literal["high", "medium", "low"]
    message: str
    clause: str | None = None


# --------------------------------------------------------------------------- quotes


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"')
    text = text.replace("”", '"').replace("–", "-").replace("—", "-")
    # PDF extraction breaks lines and hyphenates; compare on collapsed whitespace.
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    q = _normalise(quote).strip(" .\"'")
    return bool(q) and q in _normalise(text)


def verify_quotes(extraction: ContractExtraction, text: str) -> list[str]:
    """Clause keys whose quote is NOT found in the document."""
    return [k for k, c in extraction.clauses().items() if not quote_in_text(c.quote, text)]


# --------------------------------------------------------------------------- risk rules

_NON_REFUNDABLE = re.compile(r"non[- ]?refundable|forfeit|retained|not be refunded", re.I)
_ONE_SIDED = re.compile(
    r"\bnot (be )?(responsible|liable)\b|\bno liability\b"
    r"|client (is|shall be) (responsible|liable) for all\b|\bat its (sole )?discretion\b",
    re.I,
)
HIGH_ADVANCE_PERCENT = 50.0


def risk_flags(extraction: ContractExtraction, text: str, unverified: list[str]) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    c = extraction
    refundable = c.advance_refundable
    pay_text = (
        (c.payment_schedule.quote if c.payment_schedule else "")
        + " "
        + (c.cancellation.quote if c.cancellation else "")
    )
    if refundable is None and _NON_REFUNDABLE.search(pay_text):
        refundable = False
    if (
        c.advance_percent is not None
        and c.advance_percent > HIGH_ADVANCE_PERCENT
        and not refundable
    ):
        flags.append(
            RiskFlag(
                code="high_nonrefundable_advance",
                severity="high",
                message=f"Non-refundable advance of {c.advance_percent:g}% (above "
                f"{HIGH_ADVANCE_PERCENT:g}%).",
                clause="payment_schedule",
            )
        )
    if c.cancellation is None:
        flags.append(
            RiskFlag(
                code="no_cancellation_clause",
                severity="high",
                message="No cancellation clause: the client's exit terms are undefined.",
            )
        )
    if c.refund is None:
        flags.append(
            RiskFlag(
                code="no_refund_clause",
                severity="medium",
                message="No refund clause: how and when money comes back is undefined.",
            )
        )
    if c.force_majeure is None:
        flags.append(
            RiskFlag(
                code="no_force_majeure",
                severity="medium",
                message="No force majeure clause (weather, government orders, epidemics).",
            )
        )
    if c.liability is not None and _ONE_SIDED.search(c.liability.quote):
        flags.append(
            RiskFlag(
                code="one_sided_liability",
                severity="medium",
                message="Liability is one-sided against the client.",
                clause="liability",
            )
        )
    if c.deliverables is None:
        flags.append(
            RiskFlag(
                code="no_deliverables",
                severity="medium",
                message="Deliverables are not specified.",
            )
        )
    for key in unverified:
        flags.append(
            RiskFlag(
                code="possible_hallucination",
                severity="high",
                message=f"The quoted '{key}' text was not found in the document.",
                clause=key,
            )
        )
    if find_suspicious(text):
        flags.append(
            RiskFlag(
                code="suspicious_instructions",
                severity="high",
                message="The document contains instruction-like text aimed at AI systems. It "
                "was ignored; review the document manually.",
            )
        )
    return flags


# --------------------------------------------------------------------------- rule-based extraction

_HEADINGS: dict[str, ClauseKey] = {
    "payment schedule": "payment_schedule",
    "payment terms": "payment_schedule",
    "payments": "payment_schedule",
    "payment": "payment_schedule",
    "cancellation": "cancellation",
    "cancellation policy": "cancellation",
    "termination": "cancellation",
    "refunds": "refund",
    "refund": "refund",
    "refund policy": "refund",
    "liability": "liability",
    "limitation of liability": "liability",
    "indemnity": "liability",
    "force majeure": "force_majeure",
    "deliverables": "deliverables",
    "scope of services": "deliverables",
    "services": "deliverables",
}
_HEADING_LINE = re.compile(r"^\s*(?:\d+[.)]\s*)?([A-Za-z][A-Za-z ]{2,40}?)\s*:?\s*$")
_ADVANCE_PATTERNS = [
    re.compile(r"advance of (\d{1,3}(?:\.\d+)?)\s*%", re.I),
    re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of [\w\s]{0,30}?)?advance", re.I),
    re.compile(r"advance[^.%]{0,40}?(\d{1,3}(?:\.\d+)?)\s*%", re.I),
]


def _sections(text: str) -> dict[ClauseKey, str]:
    found: dict[ClauseKey, list[str]] = {}
    current: ClauseKey | None = None
    for line in text.splitlines():
        m = _HEADING_LINE.match(line)
        key = _HEADINGS.get(m.group(1).strip().lower()) if m else None
        if key is not None:
            current = key
            found.setdefault(key, [])
            continue
        if current is not None and line.strip():
            found[current].append(line.strip())
    return {k: " ".join(v) for k, v in found.items() if v}


def _first_sentence(body: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", body)
    return (m.group(1) if m else body).strip()


def heuristic_extract(text: str) -> ContractExtraction:
    sections = _sections(text)
    data: dict[str, object] = {}
    for key, body in sections.items():
        data[key] = ExtractedClause(quote=_first_sentence(body), summary=body[:300])
    pay = sections.get("payment_schedule", "")
    for pattern in _ADVANCE_PATTERNS:
        m = pattern.search(pay)
        if m:
            data["advance_percent"] = float(m.group(1))
            break
    money_text = pay + " " + sections.get("cancellation", "")
    if _NON_REFUNDABLE.search(money_text):
        data["advance_refundable"] = False
    elif re.search(r"advance (is|will be) refunded|of the advance is refunded", money_text, re.I):
        data["advance_refundable"] = True
    return ContractExtraction.model_validate(data)
