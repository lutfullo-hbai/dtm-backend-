"""Structured logging (Twelve-Factor XI): one event per line on stdout.

`configure_logging` runs once at startup (from `create_app`). Every log line, whether
it comes from structlog or from a stdlib logger (uvicorn, SQLAlchemy, Alembic), passes
through the same processor chain and is rendered as one JSON object per line
(LOG_FORMAT=json) or human-readable text (LOG_FORMAT=console):

    {"event": "http_request", "level": "info", "logger": "app.http",
     "request_id": "...", "timestamp": "2026-11-12T09:30:00.123456Z", ...}

`request_id` is present while a request is being served: the request-context
middleware binds it in structlog's contextvars.

Uvicorn's access log is redundant with the middleware's `http_request` event. Run
uvicorn with `--no-access-log`; if that flag is forgotten, the `uvicorn.access`
logger is disabled here anyway, so no access line (with its query string) is written.

Tracebacks are rendered as plain text in both formats, never with local variables:
the locals of a request's frames include the ASGI scope (headers, Authorization,
query string).

Database values are kept out of every line by app.core.log_redaction.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from app.core.config import LogFormat, LogLevel
from app.core.log_redaction import format_exception, redact_values

# uvicorn installs its own handlers on these; they are removed so records propagate
# to the root handler and are rendered like every other line.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
_UVICORN_ACCESS_LOGGER = "uvicorn.access"
# SQLAlchemy logs every statement, with its bound parameters, at INFO as soon as its
# logger is enabled for INFO, which inheriting the root level would do. Statements are
# logged only by the slow-query listener, without parameters. httpx logs every request
# URL, query string (and any token in it) included, at INFO.
_QUIET_LOGGERS = ("sqlalchemy", "httpx")


class _StdoutHandler(logging.Handler):
    """Write each formatted record as one line to the current `sys.stdout`, flushed.

    `sys.stdout` is looked up per record rather than captured once, so the handler
    keeps working when stdout is replaced (test capture, a reattached stream).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()  # unbuffered: a line is visible as soon as it is logged
        except Exception:  # noqa: BLE001  # logging.Handler contract: never raise from emit
            self.handleError(record)


def _shared_processors() -> list[Processor]:
    """Processors applied to structlog events and stdlib records alike."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
    ]


def _renderer(log_format: LogFormat) -> list[Processor]:
    # The traceback becomes a plain "exception" string first (no locals, in either
    # format), so it can be redacted before it is rendered.
    formatted = [format_exception, redact_values]
    if log_format == "console":
        console = structlog.dev.ConsoleRenderer(
            colors=sys.stdout.isatty(), exception_formatter=structlog.dev.plain_traceback
        )
        return [*formatted, console]
    return [*formatted, structlog.processors.JSONRenderer()]


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """Route structlog and stdlib logging to stdout through one processor chain.

    Safe to call again (tests build many applications): the handler installed by a
    previous call is replaced, and handlers installed by others are left alone.
    """
    shared = _shared_processors()
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Not cached, so a later configure_logging (or structlog.testing.capture_logs)
        # takes effect for loggers created at import time too.
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_renderer(log_format),
        ],
    )
    handler = _StdoutHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _StdoutHandler)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        # uvicorn's dictConfig (or --log-level) pins INFO; inherit LOG_LEVEL instead.
        uvicorn_logger.setLevel(logging.NOTSET)
    access = logging.getLogger(_UVICORN_ACCESS_LOGGER)
    access.handlers.clear()
    access.propagate = False
    access.disabled = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A structlog logger whose `logger` field is `name` (use the module's `__name__`)."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
