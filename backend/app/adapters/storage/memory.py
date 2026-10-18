"""In-memory object storage for tests."""

from __future__ import annotations


class MemoryStorage:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        return self._objects[key][0]

    async def ping(self) -> bool:
        return True
