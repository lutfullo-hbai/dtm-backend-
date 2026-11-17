"""Keep database values out of log lines: the processors app.core.logging installs.

Database values are redacted from every string member (the exception
text included): those asyncpg quotes client-side ("query argument $N: ..."), those
PostgreSQL quotes server-side ('invalid input syntax for type integer: "..."') and
every DETAIL line (a unique, foreign-key, check or not-null violation repeats the row's
values there). A server error's value-free identifiers are logged instead, as
`db_error` (sqlstate, constraint_name, table_name, column_name).
"""

import re
import sys
import traceback
from collections.abc import Iterator

from structlog.typing import EventDict, WrappedLogger

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


def format_exception(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
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


def redact_values(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Redact database values in every string member (event, exception, str(error))."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = _redact(value)
    return event_dict
