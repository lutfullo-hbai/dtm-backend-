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
query string). Database values are redacted from every string member (the exception
text included): those asyncpg quotes client-side ("query argument $N: ..."), those
PostgreSQL quotes server-side ('invalid input syntax for type integer: "..."') and
every DETAIL line (a unique, foreign-key, check or not-null violation repeats the row's
values there). A server error's value-free identifiers are logged instead, as
`db_error` (sqlstate, constraint_name, table_name, column_name).
"""

import logging
import re
import sys
import traceback
from collections.abc import Iterator

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.core.config import LogFormat, LogLevel

# uvicorn installs its own handlers on these; they are removed so records propagate
# to the root handler and are rendered like every other line.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
_UVICORN_ACCESS_LOGGER = "uvicorn.access"
# SQLAlchemy logs every statement, with its bound parameters, at INFO as soon as its
# logger is enabled for INFO, which inheriting the root level would do. Statements are
# logged only by the slow-query listener, without parameters. httpx logs every request
# URL, query string (and any token in it) included, at INFO.
_QUIET_LOGGERS = ("sqlalchemy", "httpx")
# asyncpg quotes the rejected bound value (and repeats it in a parenthesised reason)
# in "invalid input for query argument $1: 'value' (reason)", raised from the codec's
# own error ("invalid UUID 'value': ..."); SQLAlchemy's hide_parameters removes
# neither. The line pattern is everything after the colon, to the end of the line.
_QUERY_ARGUMENT_MARKER = "query argument $"
_REDACTED = "[REDACTED]"
# Each pattern keeps its first group and replaces the value after it, to the end of the
# line: PostgreSQL does not escape a quote inside the value it quotes, so matching up to
# the next quote could leave the rest of the value behind. `\n` (not `\s`) ends a value,
# so a pattern never reaches into the next line of a traceback.
_VALUE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        # asyncpg, client-side: invalid input for query argument $1: 'value' (reason)
        r"(query argument \$\d+:)[^\n]*",
        # Server-side: the row's values (unique, foreign key, check, not null, numeric
        # overflow) or the offending input (JSON token) are in the DETAIL line.
        r"(\bDETAIL:)[^\n]*",
        # invalid input syntax for type integer: "value"; invalid input value for enum
        r"(invalid input (?:syntax|value) for (?:type|enum) [^:\n]+:)[^\n]*",
        # date/time field value out of range: "value"; malformed array literal: "value"
        r"(date/time field value out of range:)[^\n]*",
        r"(malformed [a-z]+ literal:)[^\n]*",
        # value "99999999999" is out of range for type integer
        r"(\bvalue) \"[^\n]*\"(?= is out of range for type)",
    )
)
# Value-free identifiers of a server error (asyncpg's PostgresError attributes).
_DB_ERROR_FIELDS = ("sqlstate", "constraint_name", "table_name", "column_name")


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


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The exception and every cause/context it was raised from (each once)."""
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def _driver_value_messages(error: BaseException) -> list[str]:
    """Messages of the errors a "query argument" error was raised from (codec errors).

    Origins that are themselves "query argument" errors are left to the line pattern.
    Longest first, so a message containing another is replaced whole.
    """
    messages: set[str] = set()
    for current in _chain(error):
        if _QUERY_ARGUMENT_MARKER in str(current):
            for origin in (current.__cause__, current.__context__):
                text = str(origin) if origin is not None else ""
                if text and _QUERY_ARGUMENT_MARKER not in text:
                    messages.add(text)
    return sorted(messages, key=len, reverse=True)


def _server_errors(error: BaseException) -> list[BaseException]:
    """PostgreSQL errors in the chain (asyncpg's PostgresError: a `sqlstate` and a `detail`)."""
    return [
        current
        for current in _chain(error)
        if isinstance(getattr(current, "sqlstate", None), str) and hasattr(current, "detail")
    ]


def _server_details(errors: list[BaseException]) -> list[str]:
    """The DETAIL texts of server errors, longest first (they carry the row's values)."""
    details = {getattr(error, "detail", None) for error in errors}
    return sorted((d for d in details if isinstance(d, str) and d), key=len, reverse=True)


def _db_error_fields(errors: list[BaseException]) -> dict[str, str]:
    """Value-free identifiers of the innermost server error (the one PostgreSQL raised)."""
    for error in reversed(errors):
        fields = {name: getattr(error, name, None) for name in _DB_ERROR_FIELDS}
        present = {name: value for name, value in fields.items() if isinstance(value, str)}
        if present:
            return present
    return {}


def _redact(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(rf"\1 {_REDACTED}", text)
    return text


def _format_exception(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Render `exc_info` as a plain-text `exception` member: no locals, values redacted.

    A PostgreSQL error's identifiers are added as `db_error`, so the failing constraint
    or column is still known once the values are gone.
    """
    exc_info = event_dict.pop("exc_info", None)
    if isinstance(exc_info, tuple):
        exc_info = exc_info[1]
    elif exc_info is True:
        exc_info = sys.exc_info()[1]
    if isinstance(exc_info, BaseException):
        text = "".join(traceback.format_exception(exc_info)).rstrip("\n")
        server_errors = _server_errors(exc_info)
        for message in [*_server_details(server_errors), *_driver_value_messages(exc_info)]:
            text = text.replace(message, _REDACTED)
        event_dict["exception"] = text
        db_error = _db_error_fields(server_errors)
        if db_error:
            event_dict.setdefault("db_error", db_error)
    return event_dict


def _redact_values(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Redact database values in every string member (event, exception, str(error))."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = _redact(value)
    return event_dict


def _renderer(log_format: LogFormat) -> list[Processor]:
    # The traceback becomes a plain "exception" string first (no locals, in either
    # format), so it can be redacted before it is rendered.
    formatted = [_format_exception, _redact_values]
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
