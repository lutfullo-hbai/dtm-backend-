"""Count SQL statements, so tests can put a ceiling on round-trips (N+1 control)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection


class QueryCounter:
    """Records every statement sent to the database while the block runs.

    Listens on the `Engine` class by default, so it sees every engine in the
    process, including ones created inside the block (an app's lifespan engine).
    Pass a specific engine to count only that one. Pool pre-pings are not SQL
    statements and are not counted.
    """

    def __init__(self, target: Engine | type[Engine] = Engine) -> None:
        self._target = target
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        """Number of statements recorded so far."""
        return len(self.statements)

    def __enter__(self) -> Self:
        event.listen(self._target, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_: object) -> None:
        event.remove(self._target, "before_cursor_execute", self._record)

    def _record(self, _connection: Connection, _cursor: object, statement: str, *_: object) -> None:
        self.statements.append(statement)


@contextmanager
def assert_max_queries(
    limit: int, target: Engine | type[Engine] = Engine
) -> Iterator[QueryCounter]:
    """Fail if the block sends more than `limit` statements; lists them on failure."""
    with QueryCounter(target) as counter:
        yield counter
    if counter.count > limit:
        listing = "\n".join(
            f"  {number}. {sql}" for number, sql in enumerate(counter.statements, 1)
        )
        message = f"expected at most {limit} SQL statements, got {counter.count}:\n{listing}"
        raise AssertionError(message)
