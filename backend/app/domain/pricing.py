"""Price benchmarking: where a quote sits against the market for a category in a city."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (same as numpy's default), q in [0, 100]."""
    if not sorted_values:
        raise ValueError("no values")
    pos = (len(sorted_values) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


@dataclass(frozen=True)
class Benchmark:
    sample_size: int
    p25: float
    median: float
    p75: float
    quote: float | None
    quote_percentile: float | None
    position: str | None
    vs_median_pct: float | None


def typical_price(price_min: Decimal, price_max: Decimal) -> float:
    """A vendor's typical quote: midpoint of its published range."""
    return float((price_min + price_max) / 2)


def benchmark(prices: Sequence[float], quote: float | None) -> Benchmark:
    values = sorted(prices)
    p25, med, p75 = (percentile(values, q) for q in (25, 50, 75))
    if quote is None:
        return Benchmark(len(values), p25, med, p75, None, None, None, None)
    rank = sum(1 for v in values if v < quote) + 0.5 * sum(1 for v in values if v == quote)
    if quote < p25:
        position = "below typical range (cheaper than 75% of the market)"
    elif quote > p75:
        position = "above typical range (pricier than 75% of the market)"
    else:
        position = "within the typical range"
    return Benchmark(
        sample_size=len(values),
        p25=round(p25, 2),
        median=round(med, 2),
        p75=round(p75, 2),
        quote=quote,
        quote_percentile=round(100 * rank / len(values), 1),
        position=position,
        vs_median_pct=round((quote - med) / med * 100, 1) if med else None,
    )
