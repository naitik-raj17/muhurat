"""Deterministic travel-time estimates (no maps API)."""

from __future__ import annotations

import hashlib


class MockTravelTime:
    """Same venue: 0 min. Different venues in one city: 20-75 min, stable per venue pair."""

    async def minutes_between(self, origin: str, destination: str, city: str) -> int:
        if origin.strip().lower() == destination.strip().lower():
            return 0
        a, b = sorted([origin.strip().lower(), destination.strip().lower()])
        digest = hashlib.sha256(f"{city}|{a}|{b}".encode()).digest()
        return 20 + digest[0] % 56
