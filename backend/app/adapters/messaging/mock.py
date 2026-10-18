"""Mock messaging channel: records messages in memory, never contacts a provider."""

from __future__ import annotations

import hashlib

from app.ports.messaging import OutboundMessage, SendResult


class MockChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[OutboundMessage] = []
        # Tests flip this to simulate a provider outage.
        self.fail_next: int = 0

    async def send(self, message: OutboundMessage) -> SendResult:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError(f"mock {self.name} provider unavailable")
        self.sent.append(message)
        seed = f"{self.name}:{message.dedupe_key or message.to}:{len(self.sent)}"
        return SendResult(
            provider_message_id="mock-" + hashlib.sha256(seed.encode()).hexdigest()[:24],
            status="sent",
        )
