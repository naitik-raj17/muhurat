"""Object storage port (MinIO / S3)."""

from __future__ import annotations

from typing import Protocol


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def ping(self) -> bool: ...
