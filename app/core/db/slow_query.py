"""Log SQL statements slower than SLOW_QUERY_MS.

Uses stdlib logging until structured logging arrives (M0-08). Bound parameters are
never logged, because they may carry personal data or credentials.
"""

import logging
import time

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger("app.core.db")

STATEMENT_LOG_LIMIT = 500
_STARTED_KEY = "app.core.db.query_started"


def install_slow_query_logging(engine: Engine, *, threshold_ms: float) -> None:
    """Time every cursor execution on `engine`; warn when one exceeds the threshold.

    The start time lives in the connection's `info`, which is safe because one
    connection runs one statement at a time. A statement that fails leaves a stale
    start time, which the next statement on that connection overwrites.
    """

    def before_cursor_execute(connection: Connection, *_: object) -> None:
        connection.info[_STARTED_KEY] = time.perf_counter()

    def after_cursor_execute(
        connection: Connection, _cursor: object, statement: str, *_: object
    ) -> None:
        started = connection.info.pop(_STARTED_KEY, None)
        if started is None:
            return
        duration_ms = (time.perf_counter() - started) * 1000
        if duration_ms < threshold_ms:
            return
        truncated = statement[:STATEMENT_LOG_LIMIT]
        logger.warning(
            "slow_query duration_ms=%.1f statement=%s",
            duration_ms,
            truncated,
            extra={"duration_ms": round(duration_ms, 1), "statement": truncated},
        )

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
