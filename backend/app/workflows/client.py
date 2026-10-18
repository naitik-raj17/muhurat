"""Shared Temporal client (one connection per process)."""

from __future__ import annotations

import asyncio

from temporalio.client import Client

from app.core.config import get_settings

_client: Client | None = None
_lock = asyncio.Lock()


async def connect() -> Client:
    global _client
    async with _lock:
        if _client is None:
            s = get_settings()
            _client = await Client.connect(s.temporal_host, namespace=s.temporal_namespace)
    return _client
