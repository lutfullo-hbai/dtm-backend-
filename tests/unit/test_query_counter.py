"""The query counter, exercised through real engine events on in-memory SQLite."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from tests.support.query_counter import QueryCounter, assert_max_queries


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def _run(engine: Engine, *statements: str) -> None:
    with engine.connect() as connection:
        for statement in statements:
            connection.execute(text(statement))


def test_counts_statements_inside_the_block_only(engine: Engine) -> None:
    _run(engine, "SELECT 0")

    with QueryCounter() as counter:
        _run(engine, "SELECT 1", "SELECT 2")
    _run(engine, "SELECT 3")

    assert counter.statements == ["SELECT 1", "SELECT 2"]
    assert counter.count == 2


def test_can_be_limited_to_one_engine(engine: Engine) -> None:
    other = create_engine("sqlite://")

    with QueryCounter(engine) as counter:
        _run(engine, "SELECT 1")
        _run(other, "SELECT 2")
    other.dispose()

    assert counter.statements == ["SELECT 1"]


def test_assert_max_queries_passes_at_the_limit(engine: Engine) -> None:
    with assert_max_queries(2) as counter:
        _run(engine, "SELECT 1", "SELECT 2")

    assert counter.count == 2


def test_assert_max_queries_fails_above_the_limit_and_lists_statements(engine: Engine) -> None:
    with pytest.raises(AssertionError) as raised, assert_max_queries(1):
        _run(engine, "SELECT 1", "SELECT 2")

    message = str(raised.value)
    assert "expected at most 1 SQL statements, got 2" in message
    assert "1. SELECT 1" in message
    assert "2. SELECT 2" in message


class _BlockError(Exception):
    pass


def _query_then_fail(engine: Engine) -> None:
    _run(engine, "SELECT 1")
    raise _BlockError


def test_assert_max_queries_does_not_hide_the_blocks_own_error(engine: Engine) -> None:
    with pytest.raises(_BlockError), assert_max_queries(0):
        _query_then_fail(engine)
