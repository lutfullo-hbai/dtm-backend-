"""A statement killed by statement_timeout is logged as slow, whatever SLOW_QUERY_MS is."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.core.db import create_engine
from tests.support.logs import LogReader


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
