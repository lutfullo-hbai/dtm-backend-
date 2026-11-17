"""Slow-query logging and the engine's statement timeout, against the real PostgreSQL.

Thresholds are set on the listener directly (an always-exceeded or never-reached one),
so whether a statement counts as slow never depends on how fast the machine is.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.db import create_engine
from app.core.db.slow_query import STATEMENT_LOG_LIMIT, install_slow_query_logging
from tests.support.logs import LogReader

_ALWAYS = 0.000001  # threshold in ms that every statement exceeds
_NEVER = 600_000.0
_SECRET = "hunter2-password"


async def _engine(database_url: str, threshold_ms: float) -> AsyncEngine:
    engine = create_async_engine(database_url, poolclass=NullPool, hide_parameters=True)
    install_slow_query_logging(engine.sync_engine, threshold_ms=threshold_ms)
    return engine


@pytest.fixture
async def slow_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = await _engine(database_url, _ALWAYS)
    yield engine
    await engine.dispose()


@pytest.fixture
async def fast_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = await _engine(database_url, _NEVER)
    yield engine
    await engine.dispose()


async def test_slow_statement_is_logged_without_parameters(
    slow_engine: AsyncEngine, logs: LogReader
) -> None:
    async with slow_engine.connect() as connection:
        await connection.execute(text("SELECT CAST(:v AS text)"), {"v": _SECRET})

    (line,) = logs.events("slow_query")
    assert line["level"] == "warning"
    assert line["logger"] == "app.core.db"
    assert line["statement"] == "SELECT CAST($1 AS text)"
    assert isinstance(line["duration_ms"], float)
    assert _SECRET not in repr(logs.lines())


async def test_statement_faster_than_threshold_is_not_logged(
    fast_engine: AsyncEngine, logs: LogReader
) -> None:
    async with fast_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    assert logs.events("slow_query") == []


async def test_long_statement_is_truncated(slow_engine: AsyncEngine, logs: LogReader) -> None:
    long_literal = "x" * (STATEMENT_LOG_LIMIT * 2)

    async with slow_engine.connect() as connection:
        await connection.execute(text(f"SELECT '{long_literal}'"))

    (line,) = logs.events("slow_query")
    assert len(line["statement"]) == STATEMENT_LOG_LIMIT


async def test_failed_slow_statement_is_logged_without_the_server_message(
    slow_engine: AsyncEngine, logs: LogReader
) -> None:
    async with slow_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="invalid input syntax"):
            await connection.execute(
                text("SELECT CAST(CAST(:v AS text) AS integer)"), {"v": _SECRET}
            )

    (line,) = logs.events("slow_query")
    assert line["statement"] == "SELECT CAST(CAST($1 AS text) AS integer)"
    assert line["failed"] is True
    assert line["timed_out"] is False
    assert line["sqlstate"] == "22P02"
    assert line["error_type"] == "Error"  # SQLAlchemy's adapted asyncpg error, by class name
    assert "invalid input syntax" not in repr(logs.lines())
    assert _SECRET not in repr(logs.lines())


async def test_failed_fast_statement_is_not_logged(
    fast_engine: AsyncEngine, logs: LogReader
) -> None:
    async with fast_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="does not exist"):
            await connection.execute(text("SELECT * FROM missing_table"))

    assert logs.events("slow_query") == []


async def test_failed_statement_does_not_leave_its_start_time_behind(
    slow_engine: AsyncEngine, logs: LogReader
) -> None:
    async with slow_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="does not exist"):
            await connection.execute(text("SELECT * FROM missing_table"))
        assert "app.core.db.query_started" not in connection.info
        await connection.rollback()
        await connection.execute(text("SELECT 2"))

    statements = [line["statement"] for line in logs.events("slow_query")]
    assert statements == ["SELECT * FROM missing_table", "SELECT 2"]


async def test_connection_failure_is_ignored(database_url: str, logs: LogReader) -> None:
    """A rejected login is a database error, but no statement ran: nothing to time."""
    wrong_password = make_url(database_url).set(password="not-the-password")
    engine = await _engine(wrong_password.render_as_string(hide_password=False), _ALWAYS)
    try:
        with pytest.raises(DBAPIError, match="password authentication failed"):
            await engine.connect().start()
    finally:
        await engine.dispose()

    assert logs.events("slow_query") == []


async def test_cancelled_statement_is_logged_even_below_the_threshold(
    test_settings: Settings, logs: LogReader
) -> None:
    settings = test_settings.model_copy(update={"slow_query_ms": 600_000})
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET statement_timeout = 50"))
            with pytest.raises(DBAPIError, match="statement timeout"):
                await connection.execute(text("SELECT pg_sleep(:s)"), {"s": 2})
    finally:
        await engine.dispose()

    (line,) = logs.events("slow_query")
    assert line["statement"] == "SELECT pg_sleep($1)"
    assert line["failed"] is True
    assert line["timed_out"] is True
    assert line["sqlstate"] == "57014"
    assert line["duration_ms"] >= 40
    assert "canceling statement" not in repr(logs.lines())


async def test_app_engine_connections_use_the_configured_statement_timeout(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(update={"db_statement_timeout_ms": 1234})
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            value = (await connection.execute(text("SHOW statement_timeout"))).scalar_one()
    finally:
        await engine.dispose()

    assert value == "1234ms"
