"""Slow-query logging, exercised through real engine events on in-memory SQLite."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text

from app.core.db.slow_query import STATEMENT_LOG_LIMIT, install_slow_query_logging
from tests.support.logs import LogReader

_ALWAYS = 0.000001  # threshold in ms that every statement exceeds


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_statement_slower_than_threshold_is_logged(engine: Engine, logs: LogReader) -> None:
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    (line,) = logs.events("slow_query")
    assert line["level"] == "warning"
    assert line["logger"] == "app.core.db"
    assert line["statement"] == "SELECT 1"
    assert isinstance(line["duration_ms"], float)
    assert line["duration_ms"] >= 0


def test_statement_faster_than_threshold_is_not_logged(engine: Engine, logs: LogReader) -> None:
    install_slow_query_logging(engine, threshold_ms=60_000)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert logs.events("slow_query") == []


def test_parameters_are_never_logged(engine: Engine, logs: LogReader) -> None:
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)

    with engine.connect() as connection:
        connection.execute(text("SELECT :secret_value"), {"secret_value": "hunter2-password"})

    (line,) = logs.events("slow_query")
    assert line["statement"] == "SELECT ?"
    assert "hunter2-password" not in repr(logs.lines())


def test_long_statement_is_truncated(engine: Engine, logs: LogReader) -> None:
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)
    long_literal = "x" * (STATEMENT_LOG_LIMIT * 2)

    with engine.connect() as connection:
        connection.execute(text(f"SELECT '{long_literal}'"))

    (line,) = logs.events("slow_query")
    assert len(line["statement"]) == STATEMENT_LOG_LIMIT


def test_failed_slow_statement_is_logged_without_the_driver_message(
    engine: Engine, logs: LogReader
) -> None:
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)

    with engine.connect() as connection, pytest.raises(Exception, match="no such table"):
        connection.execute(text("SELECT * FROM missing_table WHERE x = :v"), {"v": "hunter2"})

    (line,) = logs.events("slow_query")
    assert line["statement"] == "SELECT * FROM missing_table WHERE x = ?"
    assert line["failed"] is True
    assert line["timed_out"] is False
    assert line["error_type"] == "OperationalError"
    assert line["sqlstate"] is None
    assert isinstance(line["duration_ms"], float)
    assert "no such table" not in repr(logs.lines())
    assert "hunter2" not in repr(logs.lines())


def test_failed_fast_statement_is_not_logged(engine: Engine, logs: LogReader) -> None:
    install_slow_query_logging(engine, threshold_ms=60_000)

    with engine.connect() as connection, pytest.raises(Exception, match="no such table"):
        connection.execute(text("SELECT * FROM missing_table"))

    assert logs.events("slow_query") == []


def test_failed_statement_does_not_leave_its_start_time_behind(
    engine: Engine, logs: LogReader
) -> None:
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)

    with engine.connect() as connection:
        with pytest.raises(Exception, match="no such table"):
            connection.execute(text("SELECT * FROM missing_table"))
        assert "app.core.db.query_started" not in connection.info
        connection.execute(text("SELECT 2"))

    statements = [line["statement"] for line in logs.events("slow_query")]
    assert statements == ["SELECT * FROM missing_table", "SELECT 2"]


def test_connection_failure_is_ignored(logs: LogReader, tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/missing-dir/db.sqlite")
    install_slow_query_logging(engine, threshold_ms=_ALWAYS)

    with pytest.raises(Exception, match="unable to open"), engine.connect():
        pass

    engine.dispose()
    assert logs.events("slow_query") == []
