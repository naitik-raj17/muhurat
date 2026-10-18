"""Outbound messaging port (WhatsApp, email)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OutboundMessage:
    channel: str  # whatsapp | email
    to: str  # E.164 phone or email address (decrypted only at send time)
    body: str
    subject: str | None = None
    dedupe_key: str | None = None


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    status: str  # sent | delivered


class MessagingChannel(Protocol):
    name: str

    async def send(self, message: OutboundMessage) -> SendResult: ...
