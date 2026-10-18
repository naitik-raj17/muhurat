"""WhatsApp Cloud API channel (Meta Graph API). Enabled when WHATSAPP_ACCESS_TOKEN and
WHATSAPP_PHONE_NUMBER_ID are set. Free-form text only reaches guests inside the 24-hour
customer-care window; production invites need approved message templates (see README)."""

from __future__ import annotations

import httpx

from app.ports.messaging import OutboundMessage, SendResult

GRAPH_VERSION = "v21.0"


class WhatsAppCloudChannel:
    name = "whatsapp"

    def __init__(self, access_token: str, phone_number_id: str, timeout: float = 15.0) -> None:
        self._url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._timeout = timeout

    async def send(self, message: OutboundMessage) -> SendResult:
        body = {
            "messaging_product": "whatsapp",
            "to": message.to.lstrip("+"),
            "type": "text",
            "text": {"body": message.body},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=body, headers=self._headers)
        resp.raise_for_status()
        return SendResult(provider_message_id=resp.json()["messages"][0]["id"], status="sent")
