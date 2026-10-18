"""Payments port. Executing payments is Tier 3: humans only, never agents."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PaymentResult:
    provider_payment_id: str
    status: str


class PaymentProvider(Protocol):
    async def pay(self, amount_inr: Decimal, reference: str) -> PaymentResult: ...
