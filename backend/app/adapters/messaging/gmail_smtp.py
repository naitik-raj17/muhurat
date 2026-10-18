"""Email over Gmail SMTP with an app password. Enabled when GMAIL_SMTP_USER and
GMAIL_SMTP_APP_PASSWORD are set. smtplib is blocking, so sends run in a worker thread."""

from __future__ import annotations

import asyncio
import smtplib
import uuid
from email.message import EmailMessage

from app.ports.messaging import OutboundMessage, SendResult


class GmailSmtpChannel:
    name = "email"

    def __init__(self, user: str, app_password: str) -> None:
        self._user = user
        self._password = app_password

    def _send(self, message: OutboundMessage) -> SendResult:
        msg_id = f"<{uuid.uuid4().hex}@wedding-ai>"
        msg = EmailMessage()
        msg["From"] = self._user
        msg["To"] = message.to
        msg["Subject"] = message.subject or "Wedding invitation"
        msg["Message-ID"] = msg_id
        msg.set_content(message.body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(msg)
        return SendResult(provider_message_id=msg_id, status="sent")

    async def send(self, message: OutboundMessage) -> SendResult:
        return await asyncio.to_thread(self._send, message)
