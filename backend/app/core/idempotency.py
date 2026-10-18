"""`Idempotency-Key` support for mutating POST endpoints.

Same key + same request from the same user -> the original response is replayed (with
`Idempotent-Replayed: true`) and the handler does not run again. Same key with a different
request -> 422. A request still in flight -> 409. 5xx responses are not stored, so the client
can retry with the same key.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.db.models import IdempotencyKey
from app.db.session import get_sessionmaker

log = get_logger(__name__)
_EXEMPT_PREFIXES = ("/api/v1/auth/", "/api/v1/webhooks/")
_MAX_KEY = 200
_UNIQUE = [IdempotencyKey.scope, IdempotencyKey.key]


def _problem(status: int, slug: str, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {
            "type": f"https://wedding-ai.example/problems/{slug}",
            "title": title,
            "status": status,
            "detail": detail,
            "request_id": get_request_id(),
        },
        status_code=status,
        media_type="application/problem+json",
    )


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/v1/")
            or scope["path"].startswith(_EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get("idempotency-key")
        claims = self._claims(headers.get("authorization", ""))
        if not key or claims is None:
            await self.app(scope, receive, send)
            return
        if len(key) > _MAX_KEY:
            await _problem(422, "validation-failed", "Invalid Idempotency-Key", "Too long")(
                scope, receive, send
            )
            return

        body = await self._read_body(receive)
        digest = hashlib.sha256(
            b"\n".join([scope["path"].encode(), scope.get("query_string", b""), body])
        ).hexdigest()
        key_scope = f"http:{claims['sub']}:{scope['path']}"

        sessions = get_sessionmaker()
        async with sessions() as s, s.begin():
            inserted = (
                await s.execute(
                    insert(IdempotencyKey)
                    .values(
                        id=uuid.uuid4(),
                        organization_id=uuid.UUID(claims["org"]),
                        scope=key_scope,
                        key=key,
                        request_hash=digest,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[IdempotencyKey.scope, IdempotencyKey.key]
                    )
                    .returning(IdempotencyKey.id)
                )
            ).scalar_one_or_none()
            existing = None
            if inserted is None:
                existing = (
                    await s.execute(
                        select(IdempotencyKey).where(
                            IdempotencyKey.scope == key_scope, IdempotencyKey.key == key
                        )
                    )
                ).scalar_one()
        if existing is not None:
            await self._replay(existing, digest)(scope, receive, send)
            return

        status = 500
        content_type = ""
        chunks: list[bytes] = []
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def capture_send(message: Message) -> None:
            nonlocal status, content_type
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = Headers(raw=message.get("headers", [])).get("content-type", "")
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        finally:
            await self._store(key_scope, key, status, content_type, b"".join(chunks))

    @staticmethod
    def _claims(auth: str) -> dict[str, Any] | None:
        if not auth.lower().startswith("bearer "):
            return None
        try:
            claims: dict[str, Any] = jwt.decode(
                auth[7:], get_settings().jwt_secret, algorithms=["HS256"]
            )
        except jwt.PyJWTError:
            return None  # the route's auth dependency will answer 401
        return claims if "sub" in claims and "org" in claims else None

    @staticmethod
    async def _read_body(receive: Receive) -> bytes:
        parts = []
        while True:
            message = await receive()
            parts.append(message.get("body", b""))
            if not message.get("more_body"):
                return b"".join(parts)

    @staticmethod
    def _replay(row: IdempotencyKey, digest: str) -> Response:
        if row.request_hash != digest:
            return _problem(
                422,
                "idempotency-key-reused",
                "Idempotency-Key reused",
                "This key was already used for a different request.",
            )
        if row.response_status is None:
            return _problem(409, "request-in-progress", "Request in progress", "Retry in a moment.")
        if row.response_body is None:
            return Response(
                status_code=row.response_status, headers={"Idempotent-Replayed": "true"}
            )
        return JSONResponse(
            row.response_body,
            status_code=row.response_status,
            headers={"Idempotent-Replayed": "true"},
        )

    @staticmethod
    async def _store(key_scope: str, key: str, status: int, content_type: str, body: bytes) -> None:
        sessions = get_sessionmaker()
        where = (IdempotencyKey.scope == key_scope, IdempotencyKey.key == key)
        async with sessions() as s, s.begin():
            if status >= 500:
                await s.execute(delete(IdempotencyKey).where(*where))
                return
            parsed: Any = None
            if body and "json" in content_type:
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = None
            await s.execute(
                update(IdempotencyKey)
                .where(*where)
                .values(response_status=status, response_body=parsed)
            )
