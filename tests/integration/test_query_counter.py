"""The query counter, exercised through real engine events against PostgreSQL."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import create_engine
from tests.support.query_counter import QueryCounter, assert_max_queries


@pytest.fixture
async def engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The application's engine (pool_pre_ping on), as tests will count it."""
    engine = create_engine(test_settings)
    yield engine
    await engine.dispose()


async def _run(engine: AsyncEngine, *statements: str) -> None:
    async with engine.connect() as connection:
        for statement in statements:
            await connection.execute(text(statement))


async def test_counts_statements_inside_the_block_only(engine: AsyncEngine) -> None:
    await _run(engine, "SELECT 0")

    with QueryCounter() as counter:
        await _run(engine, "SELECT 1", "SELECT 2")
    await _run(engine, "SELECT 3")

    assert counter.statements == ["SELECT 1", "SELECT 2"]
    assert counter.count == 2


async def test_pool_pre_ping_is_not_counted(engine: AsyncEngine) -> None:
    await _run(engine, "SELECT 0")  # the pooled connection is pre-pinged when reused

    with QueryCounter() as counter:
        await _run(engine, "SELECT 1")
        await _run(engine, "SELECT 2")

    assert counter.statements == ["SELECT 1", "SELECT 2"]


async def test_can_be_limited_to_one_engine(engine: AsyncEngine, test_settings: Settings) -> None:
    other = create_engine(test_settings)
    try:
        with QueryCounter(engine.sync_engine) as counter:
            await _run(engine, "SELECT 1")
            await _run(other, "SELECT 2")
    finally:
        await other.dispose()

    assert counter.statements == ["SELECT 1"]


async def test_assert_max_queries_passes_at_the_limit(engine: AsyncEngine) -> None:
    with assert_max_queries(2) as counter:
        await _run(engine, "SELECT 1", "SELECT 2")

    assert counter.count == 2


async def test_assert_max_queries_fails_above_the_limit_and_lists_statements(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(AssertionError) as raised, assert_max_queries(1):
        await _run(engine, "SELECT 1", "SELECT 2")

    message = str(raised.value)
    assert "expected at most 1 SQL statements, got 2" in message
    assert "1. SELECT 1" in message
    assert "2. SELECT 2" in message


class _BlockError(Exception):
    pass


async def _query_then_fail(engine: AsyncEngine) -> None:
    await _run(engine, "SELECT 1")
    raise _BlockError


async def test_assert_max_queries_does_not_hide_the_blocks_own_error(engine: AsyncEngine) -> None:
    with pytest.raises(_BlockError), assert_max_queries(0):
        await _query_then_fail(engine)
