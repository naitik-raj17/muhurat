"""Vendor ranking, hard-constraint filtering and explanations. Pure logic, no I/O.

Retrieval (keyword / vector) happens in the service; this module decides which candidates pass
the hard constraints, how they are scored, and *why* - every result carries an explanation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

_TOKEN_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)
# Common words that should not count as "matched terms".
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "for",
        "in",
        "of",
        "with",
        "to",
        "on",
        "at",
        "by",
        "or",
        "is",
        "are",
        "we",
        "our",
        "need",
        "want",
        "looking",
    ]
)

# Weights of the final score. Retrieval relevance dominates; the rest refines the order.
W_RELEVANCE = 0.45
W_STYLE = 0.20
W_PRICE = 0.20
W_RATING = 0.15


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


@dataclass(frozen=True)
class Candidate:
    """A vendor as seen by the matcher (decoupled from the ORM)."""

    id: str
    name: str
    category: str
    city: str
    price_min: Decimal
    price_max: Decimal
    styles: Sequence[str]
    languages: Sequence[str]
    rating: float
    review_count: int
    search_text: str


@dataclass(frozen=True)
class Constraints:
    category: str | None = None
    city: str | None = None
    budget_max: Decimal | None = None
    event_date: date | None = None
    styles: Sequence[str] = ()
    language: str | None = None


@dataclass
class Scored:
    candidate: Candidate
    score: float
    matched_terms: list[str]
    matched_styles: list[str]
    price_fit: str
    price_vs_budget_pct: float | None
    availability: str
    rating_note: str
    language_match: bool | None
    retrieval: dict[str, float | int | None] = field(default_factory=dict)
    summary: str = ""


def hard_filter_reason(
    c: Candidate, cons: Constraints, unavailable_ids: frozenset[str]
) -> str | None:
    """Return why a candidate fails a hard constraint, or None if it passes."""
    if cons.category and c.category != cons.category:
        return "category"
    if cons.city and c.city.strip().lower() != cons.city.strip().lower():
        return "city"
    if cons.budget_max is not None and c.price_min > cons.budget_max:
        return "budget"
    if cons.event_date is not None and c.id in unavailable_ids:
        return "availability"
    return None


def _price_fit(c: Candidate, budget: Decimal | None) -> tuple[str, float | None, float]:
    """(label, starting price as % of budget, score 0..1)."""
    if budget is None or budget <= 0:
        return "no budget given", None, 0.5
    pct = float(c.price_min / budget * 100)
    if c.price_max <= budget:
        label = "fully within budget"
        score = 1.0 - 0.3 * min(1.0, pct / 100)  # cheaper within budget is slightly better
    elif c.price_min <= budget:
        label = "starting price within budget; top packages exceed it"
        score = 0.6
    else:
        label = "above budget"
        score = 0.0
    return label, round(pct, 1), score


def _rating_note(c: Candidate) -> tuple[str, float]:
    note = f"{c.rating:.1f}/5 from {c.review_count} reviews"
    # Damp ratings with few reviews toward a neutral 3.5 (simple Bayesian average).
    prior_weight = 10
    adjusted = (c.rating * c.review_count + 3.5 * prior_weight) / (c.review_count + prior_weight)
    return note, max(0.0, min(1.0, (adjusted - 1) / 4))


def score_candidate(
    c: Candidate,
    cons: Constraints,
    query_tokens: Sequence[str],
    relevance: float,
    retrieval: dict[str, float | int | None],
) -> Scored:
    """`relevance` is the normalised (0..1) retrieval score from keyword/vector/RRF."""
    text_tokens = set(tokenize(c.search_text))
    matched_terms = sorted({t for t in query_tokens if t in text_tokens})

    wanted = {s.lower() for s in cons.styles} | {t for t in query_tokens}
    vendor_styles = {s.lower() for s in c.styles}
    matched_styles = sorted(vendor_styles & wanted)
    style_score = (
        len(matched_styles) / max(1, len({s.lower() for s in cons.styles}))
        if cons.styles
        else (1.0 if matched_styles else 0.5)
    )
    style_score = min(1.0, style_score)

    price_label, pct, price_score = _price_fit(c, cons.budget_max)
    rating_note, rating_score = _rating_note(c)
    availability = (
        f"available on {cons.event_date.isoformat()}"
        if cons.event_date
        else "no date given (availability not checked)"
    )
    language_match = None if cons.language is None else cons.language in c.languages

    score = (
        W_RELEVANCE * relevance
        + W_STYLE * style_score
        + W_PRICE * price_score
        + W_RATING * rating_score
    )

    parts: list[str] = []
    if matched_styles:
        parts.append("matches style " + ", ".join(matched_styles))
    elif matched_terms:
        parts.append("matches " + ", ".join(matched_terms[:4]))
    parts.append(price_label)
    if cons.event_date:
        parts.append(availability)
    parts.append(f"rated {c.rating:.1f}")
    if language_match:
        parts.append(f"speaks {cons.language}")

    return Scored(
        candidate=c,
        score=round(score, 4),
        matched_terms=matched_terms,
        matched_styles=matched_styles,
        price_fit=price_label,
        price_vs_budget_pct=pct,
        availability=availability,
        rating_note=rating_note,
        language_match=language_match,
        retrieval=retrieval,
        summary="; ".join(parts).capitalize() + ".",
    )


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> dict[str, float]:
    """Standard RRF: score(d) = sum over lists of 1 / (k + rank(d)), rank starting at 1."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


def normalise(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    if top <= 0:
        return dict.fromkeys(scores, 0.0)
    return {k: v / top for k, v in scores.items()}
