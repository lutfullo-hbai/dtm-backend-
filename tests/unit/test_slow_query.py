"""Slow-query logging, exercised through real engine events on in-memory SQLite."""

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from app.core.db.slow_query import STATEMENT_LOG_LIMIT, install_slow_query_logging

_LOGGER = "app.core.db"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def _slow_query_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == _LOGGER]


def test_statement_slower_than_threshold_is_logged(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    install_slow_query_logging(engine, threshold_ms=0.000001)

    with caplog.at_level(logging.WARNING, logger=_LOGGER), engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    (record,) = _slow_query_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith("slow_query duration_ms=")
    assert record.statement == "SELECT 1"  # type: ignore[attr-defined]  # set via `extra`
    assert record.duration_ms >= 0  # type: ignore[attr-defined]  # set via `extra`


def test_statement_faster_than_threshold_is_not_logged(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    install_slow_query_logging(engine, threshold_ms=60_000)

    with caplog.at_level(logging.WARNING, logger=_LOGGER), engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert _slow_query_records(caplog) == []


def test_parameters_are_never_logged(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    install_slow_query_logging(engine, threshold_ms=0.000001)

    with caplog.at_level(logging.WARNING, logger=_LOGGER), engine.connect() as connection:
        connection.execute(text("SELECT :secret_value"), {"secret_value": "hunter2-password"})

    (record,) = _slow_query_records(caplog)
    assert "hunter2-password" not in record.getMessage()
    assert "hunter2-password" not in repr(record.__dict__)


def test_long_statement_is_truncated(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    install_slow_query_logging(engine, threshold_ms=0.000001)
    long_literal = "x" * (STATEMENT_LOG_LIMIT * 2)

    with caplog.at_level(logging.WARNING, logger=_LOGGER), engine.connect() as connection:
        connection.execute(text(f"SELECT '{long_literal}'"))

    (record,) = _slow_query_records(caplog)
    assert len(record.statement) == STATEMENT_LOG_LIMIT  # type: ignore[attr-defined]  # via `extra`


def test_failed_statement_does_not_break_timing_of_the_next(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    install_slow_query_logging(engine, threshold_ms=0.000001)

    with caplog.at_level(logging.WARNING, logger=_LOGGER), engine.connect() as connection:
        with pytest.raises(Exception, match="no such table"):
            connection.execute(text("SELECT * FROM missing_table"))
        connection.execute(text("SELECT 2"))

    statements = [record.statement for record in _slow_query_records(caplog)]  # type: ignore[attr-defined]  # via `extra`
    assert statements == ["SELECT 2"]
