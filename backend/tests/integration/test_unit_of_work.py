"""The request unit of work commits before the response is sent.

Regression test: with FastAPI's default dependency scope, `get_db` committed after the client
already had its 200. A fast follow-up read (the UI refetching after "Approve") then saw stale
data, and a failing commit could no longer change the response.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy import event, text

from app.api.deps import DbSession


def _app(fail_commit: bool) -> FastAPI:
    app = FastAPI()

    @app.post("/write")
    async def write(session: DbSession) -> dict[str, str]:
        await session.execute(text("SELECT 1"))  # begin a transaction
        if fail_commit:

            def boom(_session: object) -> None:
                raise RuntimeError("commit failed")

            event.listen(session.sync_session, "before_commit", boom)
        return {"status": "ok"}

    return app


async def _post(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/write")


async def test_successful_commit_returns_200() -> None:
    assert (await _post(_app(fail_commit=False))).status_code == 200


async def test_failed_commit_is_an_error_response_not_200() -> None:
    assert (await _post(_app(fail_commit=True))).status_code == 500
