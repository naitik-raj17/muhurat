"""Importing this module registers every outbox handler with the dispatcher."""

from app.services import contract_service, messaging_service, notification_service

__all__ = ["contract_service", "messaging_service", "notification_service"]
