"""Mock payment provider (Razorpay replacement when no keys are set)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.ports.payments import PaymentResult


class MockPayments:
    def __init__(self) -> None:
        self.payments: list[tuple[Decimal, str]] = []

    async def pay(self, amount_inr: Decimal, reference: str) -> PaymentResult:
        self.payments.append((amount_inr, reference))
        return PaymentResult(
            provider_payment_id=f"mockpay_{uuid.uuid4().hex[:16]}", status="captured"
        )
