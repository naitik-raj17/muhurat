"""MinIO (S3-compatible) object storage. The minio SDK is synchronous, so calls run in threads."""

from __future__ import annotations

import asyncio
import io

from minio import Minio


class MinioStorage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool):
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if not self._bucket_ready:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
            self._bucket_ready = True

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    def _get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._put, key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get, key)

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._ensure_bucket)
        except Exception:
            return False
        return True
