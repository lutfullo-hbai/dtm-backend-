"""Log SQL statements slower than SLOW_QUERY_MS, including ones that fail.

Emits the structured event `slow_query` with `duration_ms` and `statement`. Bound
parameters are never logged, because they may carry personal data or credentials.

A statement that fails is logged with `failed=true`, `error_type` (the exception
class name) and `sqlstate` (PostgreSQL's error code), never the error message, since
driver messages can quote parameter values. It is logged when
it ran longer than the threshold, and always when PostgreSQL cancelled it
(`statement_timeout`, SQLSTATE 57014), whatever SLOW_QUERY_MS is set to.
"""

import time

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext

from app.core.logging import get_logger

logger = get_logger("app.core.db")

STATEMENT_LOG_LIMIT = 500
QUERY_CANCELED_SQLSTATE = "57014"
_STARTED_KEY = "app.core.db.query_started"


def install_slow_query_logging(engine: Engine, *, threshold_ms: float) -> None:
    """Time every cursor execution on `engine`; warn when one exceeds the threshold.

    The start time lives in the connection's `info`, which is safe because one
    connection runs one statement at a time. It is popped on success and on failure,
    so a stale value never times the next statement.
    """

    def elapsed_ms(connection: Connection) -> float | None:
        started = connection.info.pop(_STARTED_KEY, None)
        return None if started is None else (time.perf_counter() - started) * 1000

    def before_cursor_execute(connection: Connection, *_: object) -> None:
        connection.info[_STARTED_KEY] = time.perf_counter()

    def after_cursor_execute(
        connection: Connection, _cursor: object, statement: str, *_: object
    ) -> None:
        duration_ms = elapsed_ms(connection)
        if duration_ms is None or duration_ms < threshold_ms:
            return
        logger.warning(
            "slow_query",
            duration_ms=round(duration_ms, 1),
            statement=statement[:STATEMENT_LOG_LIMIT],
        )

    def handle_error(context: ExceptionContext) -> None:
        if context.connection is None:  # failed to connect: no statement ran
            return
        duration_ms = elapsed_ms(context.connection)
        if duration_ms is None:
            return
        error = context.original_exception
        sqlstate = getattr(error, "sqlstate", None)
        timed_out = sqlstate == QUERY_CANCELED_SQLSTATE
        if duration_ms < threshold_ms and not timed_out:
            return
        logger.warning(
            "slow_query",
            duration_ms=round(duration_ms, 1),
            statement=(context.statement or "")[:STATEMENT_LOG_LIMIT],
            failed=True,
            timed_out=timed_out,
            error_type=type(error).__name__,
            sqlstate=sqlstate,
        )

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "handle_error", handle_error)
