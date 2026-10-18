"""Budget forecasting: final spend as a multiple of the planned spend (scikit-learn, no LLM).

Training data is synthetic wedding history generated deterministically (the same generator
family as the seed data). The model predicts `final / planned` from features known mid-planning;
three gradient-boosting models give the 10th, 50th and 90th percentile.

`BudgetForecaster` is the hook for a managed model later (e.g. Vertex AI AutoML tabular):
implement `predict` and swap it in `get_forecaster()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

# Relative cost level of each city (1.0 = average); unknown cities get 1.0.
CITY_COST_INDEX = {
    "mumbai": 1.3,
    "delhi": 1.2,
    "bengaluru": 1.15,
    "udaipur": 1.25,
    "jaipur": 1.05,
    "hyderabad": 1.0,
    "kolkata": 0.95,
    "patna": 0.8,
}
FEATURES = (
    "planned_lakh",
    "guests",
    "days",
    "city_index",
    "months_to_go",
    "booked_fraction",
    "spent_fraction",
)


@dataclass(frozen=True)
class ForecastInput:
    planned_lakh: float  # sum of planned budget lines, in lakh INR
    guests: int
    days: int
    city_index: float
    months_to_go: float
    booked_fraction: float  # planned / total budget
    spent_fraction: float  # actual / planned

    def vector(self) -> list[float]:
        return [float(getattr(self, f)) for f in FEATURES]


@dataclass(frozen=True)
class ForecastRatio:
    p10: float
    p50: float
    p90: float


class BudgetForecaster(Protocol):
    name: str

    def predict(self, x: ForecastInput) -> ForecastRatio: ...


def synthetic_history(n: int = 800, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Past weddings: overruns grow with guest count, city cost, little booked early, and
    long lead times (more room for scope creep). Noise is heavy-tailed, like real budgets."""
    rng = np.random.default_rng(seed)
    planned = rng.uniform(10, 150, n)
    guests = rng.integers(80, 1200, n)
    days = rng.integers(1, 5, n)
    city = rng.choice(list(CITY_COST_INDEX.values()), n)
    months = rng.uniform(0, 12, n)
    booked = rng.uniform(0.3, 1.1, n)
    spent = rng.uniform(0, 1, n) * np.clip(1.2 - months / 12, 0.1, 1.0)
    ratio = (
        1.0
        + 0.00012 * (guests - 300)
        + 0.12 * (city - 1.0)
        + 0.10 * (1.0 - np.clip(booked, 0, 1))
        + 0.008 * months
        + 0.025 * (days - 1)
        - 0.04 * spent
        + rng.standard_t(4, n) * 0.04
    )
    x = np.column_stack([planned, guests, days, city, months, booked, spent])
    return x, np.clip(ratio, 0.75, 1.8)


class SklearnForecaster:
    name = "sklearn-gbr-quantile-v1"

    def __init__(self) -> None:
        x, y = synthetic_history()
        self._models = {
            q: GradientBoostingRegressor(
                loss="quantile", alpha=q, n_estimators=80, max_depth=3, random_state=0
            ).fit(x, y)
            for q in (0.1, 0.5, 0.9)
        }

    def predict(self, x: ForecastInput) -> ForecastRatio:
        row = np.array([x.vector()])
        p10, p50, p90 = (float(self._models[q].predict(row)[0]) for q in (0.1, 0.5, 0.9))
        # Quantile models are fitted independently; keep the band ordered.
        lo, mid, hi = sorted((p10, p50, p90))
        return ForecastRatio(lo, mid, hi)


@lru_cache
def get_forecaster() -> BudgetForecaster:
    return SklearnForecaster()
