"""Travel-time port used by timeline conflict detection."""

from __future__ import annotations

from typing import Protocol


class TravelTimeProvider(Protocol):
    async def minutes_between(self, origin: str, destination: str, city: str) -> int: ...
