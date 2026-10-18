"""Pinecone implementation over its REST data-plane API (no SDK dependency).

Enabled only when PINECONE_API_KEY is set. Vendors are upserted with metadata so queries can
filter to the global catalogue plus the caller's organization. Not exercised by the test suite
(which never calls external services); see README "Enabling real providers".
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.ports.vector import VectorQuery, VectorRecord

_API_VERSION = "2025-04"
_GLOBAL = "global"


class PineconeStore:
    name = "pinecone"

    def __init__(self, api_key: str, index_name: str, timeout: float = 10.0) -> None:
        self._headers = {"Api-Key": api_key, "X-Pinecone-API-Version": _API_VERSION}
        self._index_name = index_name
        self._host: str | None = None
        self._timeout = timeout

    async def _index_host(self, client: httpx.AsyncClient) -> str:
        if self._host is None:
            r = await client.get(
                f"https://api.pinecone.io/indexes/{self._index_name}", headers=self._headers
            )
            r.raise_for_status()
            self._host = "https://" + r.json()["host"]
        return self._host

    async def upsert(self, records: list[VectorRecord]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            host = await self._index_host(client)
            for start in range(0, len(records), 100):
                batch = records[start : start + 100]
                body = {
                    "vectors": [
                        {
                            "id": str(r.id),
                            "values": r.vector,
                            "metadata": {
                                "organization_id": str(r.organization_id or _GLOBAL),
                                "category": r.category,
                                "city": r.city.lower(),
                            },
                        }
                        for r in batch
                    ]
                }
                resp = await client.post(f"{host}/vectors/upsert", json=body, headers=self._headers)
                resp.raise_for_status()

    async def query(self, q: VectorQuery) -> list[tuple[uuid.UUID, float]]:
        flt: dict[str, Any] = {"organization_id": {"$in": [str(q.organization_id), _GLOBAL]}}
        if q.category:
            flt["category"] = {"$eq": q.category}
        if q.city:
            flt["city"] = {"$eq": q.city.strip().lower()}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            host = await self._index_host(client)
            resp = await client.post(
                f"{host}/query",
                json={"vector": q.vector, "topK": q.top_k, "filter": flt},
                headers=self._headers,
            )
            resp.raise_for_status()
            return [(uuid.UUID(m["id"]), float(m["score"])) for m in resp.json().get("matches", [])]
