"""Keep database values out of log lines: the processors app.core.logging installs.

Database errors are logged by allow-list: an exception raised by the database layer
(SQLAlchemy's StatementError/DBAPIError and its adapted driver error, any asyncpg
error, and the codec error asyncpg chains when it rejects a bound value), anywhere in
the logged exception's causes, contexts or exception-group members, keeps its type and
frames, but its message is replaced by `<message withheld: database error>`. Those
messages quote values: rejected bound parameters, uncastable input, a violating row in
DETAIL, the text of a PL/pgSQL RAISE. A server error's value-free identifiers are
logged instead, as `db_error` (sqlstate, constraint_name, table_name, column_name,
schema_name, data_type_name).

Rule for application code: never put `str(error)` (or `repr`) of a database error in a
log field; log the exception itself with `logger.exception(...)` or `exc_info=True`, so
the allow-list sees it. As a secondary layer only, every string in the event, nested
dict/list/tuple values included, is withheld whole if it is str() of a SQLAlchemy
statement error ("[SQL: ...]"), and otherwise has the value patterns below redacted.
Patterns cannot recognise every message PostgreSQL (or asyncpg, whose own str() carries
no marker) can produce, which is why the rule exists.
"""

import re
import sys
import traceback
from collections.abc import Iterator

from sqlalchemy.exc import EmulatedDBAPIException, StatementError
from structlog.typing import EventDict, WrappedLogger

# The allow-list: messages of these errors (and of what they were raised from) are never
# logged. EmulatedDBAPIException is SQLAlchemy's adapted driver error (DBAPIError.orig).
_DATABASE_ERROR_TYPES = (StatementError, EmulatedDBAPIException)
_DRIVER_MODULE_PREFIX = "asyncpg"
_WITHHELD = "<message withheld: database error>"
# str() of a SQLAlchemy statement error, put in a log field by application code: it
# always carries the statement ("[SQL: ...]"). str() of the driver error alone carries no
# marker; the rule in this module's docstring covers it.
_DATABASE_MESSAGE_MARKERS = ("[SQL: ",)
_REDACTED = "[REDACTED]"
_MAX_DEPTH = 10  # nesting levels of dict/list/tuple values redacted in a log event
# Secondary layer, for strings that are not exceptions. Each pattern keeps its first
# group and replaces the value after it, to the end of the line: PostgreSQL does not
# escape a quote inside the value it quotes, so matching up to the next quote could
# leave the rest of the value behind. `\n` (not `\s`) ends a value, so a pattern never
# reaches into the next line.
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
# Value-free identifiers of a server error (attributes of asyncpg's PostgresError).
_DB_ERROR_FIELDS = (
    "sqlstate",
    "constraint_name",
    "table_name",
    "column_name",
    "schema_name",
    "data_type_name",
)


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The exception, every cause/context and every exception-group member (each once)."""
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def _is_database_error(error: BaseException) -> bool:
    """Raised by the database layer: SQLAlchemy's statement/DBAPI wrappers or asyncpg."""
    return isinstance(error, _DATABASE_ERROR_TYPES) or type(error).__module__.startswith(
        _DRIVER_MODULE_PREFIX
    )


def _withheld(error: BaseException) -> set[int]:
    """Ids of the exceptions whose message is withheld: database errors and their origins.

    An origin is what a database error was raised from (`__cause__`, else `__context__`,
    and SQLAlchemy's `orig`): asyncpg chains the codec error that rejected a bound value
    ("invalid UUID 'value'"), which is a plain ValueError or TypeError.
    """
    ids: set[int] = set()
    for current in _chain(error):
        if _is_database_error(current):
            origins = (current.__cause__ or current.__context__, getattr(current, "orig", None))
            ids.update(id(e) for e in (current, *origins) if isinstance(e, BaseException))
    return ids


def _withhold_messages(
    rendered: traceback.TracebackException, error: BaseException, withheld: set[int]
) -> None:
    """Replace the message of every withheld exception in `rendered` (type and frames stay).

    `rendered` mirrors `error`: a child is rendered exactly for the matching cause,
    context or group member, so both trees are walked together.
    """
    pending = [(rendered, error)]
    while pending:
        node, current = pending.pop()
        if id(current) in withheld:
            # CPython's TracebackException keeps the message in `_str` (3.10 to 3.14;
            # tests/integration/test_query_argument_redaction.py fails if that changes).
            node._str = _WITHHELD  # type: ignore[attr-defined]  # private, not in typeshed
            node.__notes__ = None
        children = [(node.__cause__, current.__cause__), (node.__context__, current.__context__)]
        if node.exceptions and isinstance(current, BaseExceptionGroup):
            children.extend(zip(node.exceptions, current.exceptions, strict=False))
        pending.extend((n, e) for n, e in children if n is not None and e is not None)


def _db_error_fields(error: BaseException) -> dict[str, str]:
    """Value-free identifiers of the innermost server error (the one PostgreSQL raised)."""
    server_errors = [
        e
        for e in _chain(error)
        if type(e).__module__.startswith(_DRIVER_MODULE_PREFIX)
        and isinstance(getattr(e, "sqlstate", None), str)
    ]
    for server_error in reversed(server_errors):
        fields = {name: getattr(server_error, name, None) for name in _DB_ERROR_FIELDS}
        present = {name: value for name, value in fields.items() if isinstance(value, str)}
        if present:
            return present
    return {}


def _redact(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(rf"\1 {_REDACTED}", text)
    return text


def format_exception(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Render `exc_info` as a plain-text `exception` member: no locals, no database messages.

    A PostgreSQL error's identifiers are added as `db_error`, so the failing constraint
    or column is still known once the message is gone.
    """
    exc_info = event_dict.pop("exc_info", None)
    if isinstance(exc_info, tuple):
        exc_info = exc_info[1]
    elif exc_info is True:
        exc_info = sys.exc_info()[1]
    if isinstance(exc_info, BaseException):
        rendered = traceback.TracebackException.from_exception(exc_info)
        _withhold_messages(rendered, exc_info, _withheld(exc_info))
        event_dict["exception"] = "".join(rendered.format()).rstrip("\n")
        db_error = _db_error_fields(exc_info)
        if db_error:
            event_dict.setdefault("db_error", db_error)
    return event_dict


def _redact_value(value: object, *, whole: bool = True, depth: int = 0) -> object:
    """Redact database values in a string, and in the strings nested in dicts, lists, tuples.

    A string carrying a SQLAlchemy statement error's marker (its str()) is withheld
    whole, unless `whole` is false (the rendered traceback, whose messages
    `format_exception` already withheld). Nesting is bounded: anything deeper than
    _MAX_DEPTH (or a self-referencing structure) is replaced, not rendered.
    """
    if depth > _MAX_DEPTH:
        return _REDACTED
    if isinstance(value, str):
        if whole and any(marker in value for marker in _DATABASE_MESSAGE_MARKERS):
            return _WITHHELD
        return _redact(value)
    if isinstance(value, dict):
        return {key: _redact_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        items = [_redact_value(item, depth=depth + 1) for item in value]
        return items if isinstance(value, list) else tuple(items)
    return value


def redact_values(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Secondary layer for values app code put in the event (event, str(error), nested)."""
    for key, value in event_dict.items():
        event_dict[key] = _redact_value(value, whole=key != "exception")
    return event_dict
