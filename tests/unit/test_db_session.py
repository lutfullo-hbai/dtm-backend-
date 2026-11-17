"""The request-scoped session dependency: rollback on failure, always closed.

A recording stand-in for the session is used here on purpose, and only for the call
order. Against PostgreSQL the explicit rollback in get_session cannot be observed:
closing an AsyncSession rolls back too, so a failed request's writes are discarded
with or without it (tests/integration/test_isolation.py checks that against the real
get_session and database). What only this test pins is that the rollback runs first,
before the error propagates, and that a successful request is never rolled back.
"""

from typing import Annotated, Self

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import AsyncSession, create_session_factory, get_session


class _RecordingSession:
    """Stands in for AsyncSession and records what the dependency does with it."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.events.append("close")

    async def rollback(self) -> None:
        self.events.append("rollback")


class _HandlerError(Exception):
    pass


def _app(session: _RecordingSession) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = lambda: session

    async def succeed(_session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
        return {}

    async def fail(_session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
        raise _HandlerError

    app.add_api_route("/ok", succeed)
    app.add_api_route("/fail", fail)
    return app


async def _get(app: FastAPI, path: str) -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get(path)


async def test_session_is_closed_without_rollback_on_success() -> None:
    session = _RecordingSession()

    await _get(_app(session), "/ok")

    assert session.events == ["close"]


async def test_session_is_rolled_back_then_closed_on_failure() -> None:
    session = _RecordingSession()

    with pytest.raises(_HandlerError):
        await _get(_app(session), "/fail")

    assert session.events == ["rollback", "close"]


async def test_session_factory_keeps_attributes_after_commit() -> None:
    # Never connected: only the factory's options are inspected.
    engine = create_async_engine("postgresql+asyncpg://dtm:dtm@127.0.0.1:1/dtm")

    factory = create_session_factory(engine)
    await engine.dispose()

    assert factory.kw["expire_on_commit"] is False
