"""Each test runs in its own transaction, rolled back afterwards, even when it commits.

That holds for the test's session, the request session and the app's session factory.

The same test runs twice; whichever runs second would see the first one's row if
the rollback did not happen, so the result does not depend on test order. No
application table exists yet, so the probe row goes into `alembic_version`.
"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.db import get_session
from app.core.redis import create_redis_client

_PROBE = "isolation_probe"
_INSERT_PROBE = text("INSERT INTO alembic_version (version_num) VALUES (:probe)")
_SELECT_PROBE = text("SELECT count(*) FROM alembic_version WHERE version_num = :probe")
_REDIS_KEY = "isolation:probe"


async def _probe_rows(session_or_url: AsyncSession | str) -> int:
    if isinstance(session_or_url, AsyncSession):
        return int((await session_or_url.execute(_SELECT_PROBE, {"probe": _PROBE})).scalar_one())
    engine = create_async_engine(session_or_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(_SELECT_PROBE, {"probe": _PROBE})
            return int(result.scalar_one())
    finally:
        await engine.dispose()


@pytest.mark.parametrize("_run", ["first", "second"])
async def test_committed_write_is_invisible_elsewhere_and_gone_after_the_test(
    db_session: AsyncSession, database_url: str, _run: str
) -> None:
    assert await _probe_rows(db_session) == 0

    await db_session.execute(_INSERT_PROBE, {"probe": _PROBE})
    await db_session.commit()  # ends only the savepoint; the outer transaction stays open

    assert await _probe_rows(db_session) == 1
    assert await _probe_rows(database_url) == 0


@pytest.mark.parametrize("_run", ["first", "second"])
async def test_redis_starts_empty_for_every_test(test_settings: Settings, _run: str) -> None:
    redis = create_redis_client(test_settings)
    try:
        assert await redis.get(_REDIS_KEY) is None
        await redis.set(_REDIS_KEY, "written by a previous test")
    finally:
        await redis.aclose()


async def test_request_handlers_write_through_the_tests_session(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession, database_url: str
) -> None:
    async def write_probe(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
        await session.execute(_INSERT_PROBE, {"probe": _PROBE})
        await session.commit()
        return {}

    app.add_api_route("/test-probe", write_probe, methods=["POST"])

    response = await client.post("/test-probe")

    assert response.status_code == 200
    assert await _probe_rows(db_session) == 1
    assert await _probe_rows(database_url) == 0


@pytest.mark.parametrize("_run", ["first", "second"])
async def test_writes_through_the_apps_session_factory_stay_in_the_test(
    app: FastAPI, database_url: str, _run: str
) -> None:
    async with app.state.session_factory() as session:
        assert await _probe_rows(session) == 0
        await session.execute(_INSERT_PROBE, {"probe": _PROBE})
        await session.commit()
        assert await _probe_rows(session) == 1

    assert await _probe_rows(database_url) == 0


async def test_failed_request_rolls_back_its_writes(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    async def write_then_fail(session: Annotated[AsyncSession, Depends(get_session)]) -> None:
        await session.execute(_INSERT_PROBE, {"probe": _PROBE})
        msg = "handler failed after writing"
        raise RuntimeError(msg)

    app.add_api_route("/test-fail", write_then_fail, methods=["POST"])

    response = await client.post("/test-fail")

    assert response.status_code == 500
    assert await _probe_rows(db_session) == 0
