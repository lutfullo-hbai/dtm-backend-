"""Logging configuration in app.core.logging: one JSON object per stdout line."""

import logging
import re
from datetime import UTC, datetime

import pytest
import structlog
from sqlalchemy.exc import StatementError

from app.core.config import LogFormat
from app.core.logging import configure_logging, get_logger
from tests.support.logs import LogReader

# RFC 3339, UTC ("Z"), as written by structlog's ISO time stamper.
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def test_structlog_event_is_one_json_object_with_the_standard_members(logs: LogReader) -> None:
    get_logger("app.test").info("something_happened", answer=42)

    (line,) = logs.lines()
    assert line["event"] == "something_happened"
    assert line["level"] == "info"
    assert line["logger"] == "app.test"
    assert line["answer"] == 42
    assert _RFC3339_UTC.fullmatch(line["timestamp"])
    parsed = datetime.fromisoformat(line["timestamp"])
    assert parsed.utcoffset() == UTC.utcoffset(None)


def test_request_id_is_included_only_while_bound(logs: LogReader) -> None:
    logger = get_logger("app.test")
    structlog.contextvars.bind_contextvars(request_id="req-7")
    try:
        logger.info("inside")
    finally:
        structlog.contextvars.clear_contextvars()
    logger.info("outside")

    inside, outside = logs.lines()
    assert inside["request_id"] == "req-7"
    assert "request_id" not in outside


@pytest.mark.parametrize("name", ["uvicorn.error", "sqlalchemy.pool", "alembic.runtime", "app.x"])
def test_stdlib_loggers_use_the_same_format(logs: LogReader, name: str) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-8")
    try:
        logging.getLogger(name).warning("plain %s message", "stdlib")
    finally:
        structlog.contextvars.clear_contextvars()

    (line,) = logs.lines()
    assert line["event"] == "plain stdlib message"
    assert line["level"] == "warning"
    assert line["logger"] == name
    assert line["request_id"] == "req-8"
    assert _RFC3339_UTC.fullmatch(line["timestamp"])


def test_exception_traceback_is_one_json_member(logs: LogReader) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("app.test").exception("failed")

    (line,) = logs.lines()
    assert line["level"] == "error"
    assert "Traceback" in line["exception"]
    assert "ValueError: boom" in line["exception"]


def test_uvicorn_access_log_is_disabled_and_uvicorn_handlers_removed(logs: LogReader) -> None:
    # uvicorn's own logging config, applied before the app factory runs.
    for name in ("uvicorn", "uvicorn.access"):
        logging.getLogger(name).addHandler(logging.StreamHandler())
    configure_logging("INFO", "json")

    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /x?token=secret HTTP/1.1" 200')
    logging.getLogger("uvicorn.error").info("Started server process")

    assert [line["event"] for line in logs.lines()] == ["Started server process"]
    assert logging.getLogger("uvicorn").handlers == []
    assert logging.getLogger("uvicorn.access").handlers == []


def test_reconfiguring_keeps_one_handler_and_honours_level(logs: LogReader) -> None:
    configure_logging("WARNING", "json")
    logger = get_logger("app.test")

    logger.info("dropped")
    logger.warning("kept")

    assert [line["event"] for line in logs.lines()] == ["kept"]


def test_sqlalchemy_statement_logging_stays_off_at_debug(logs: LogReader) -> None:
    configure_logging("DEBUG", "json")

    logging.getLogger("sqlalchemy.engine.Engine").info("SELECT %s", "secret-parameter")

    assert logs.lines() == []


def test_console_format_is_human_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "console")

    get_logger("app.test").info("hello_console", answer=42)

    out = capsys.readouterr().out
    assert "hello_console" in out
    assert "answer" in out
    assert not out.lstrip().startswith("{")


def _raise_with_secret_local() -> None:
    authorization = "Bearer Sup3rSecretToken"  # a local, like the ASGI scope's headers
    msg = f"failed for {len(authorization)}-char header"
    raise RuntimeError(msg)


def test_console_traceback_never_shows_local_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", "console")

    try:
        _raise_with_secret_local()
    except RuntimeError:
        get_logger("app.test").exception("failed")

    out = capsys.readouterr().out
    assert "RuntimeError: failed for 23-char header" in out
    assert "Sup3rSecretToken" not in out
    assert "\x1b[" not in out  # stdout is not a terminal here: no colour codes


_ASYNCPG_MESSAGE = (
    "invalid input for query argument $1: "
    "'Pers0nalValue' (invalid UUID 'Pers0nalValue': length must be between 32..36)"
)
_WITHHELD = "<message withheld: database error>"


def _statement_error_from_codec() -> StatementError:
    """A statement error raised from a codec error, as SQLAlchemy chains asyncpg's."""
    try:
        msg = "invalid UUID 'Pers0nalValue': length must be between 32..36"
        raise ValueError(msg)  # the codec's own error, which asyncpg chains
    except ValueError as codec_error:
        return StatementError(_ASYNCPG_MESSAGE, "SELECT $1", None, codec_error)


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_database_error_messages_and_their_origins_are_withheld(
    capsys: pytest.CaptureFixture[str], log_format: LogFormat
) -> None:
    configure_logging("INFO", log_format)
    error = _statement_error_from_codec()

    try:
        raise error from error.orig
    except StatementError:
        logging.getLogger("sqlalchemy.x").exception("query failed")

    out = capsys.readouterr().out
    assert "Pers0nalValue" not in out
    assert f"StatementError: {_WITHHELD}" in out  # the types and frames stay
    assert f"ValueError: {_WITHHELD}" in out


def test_database_error_inside_an_exception_group_is_withheld(logs: LogReader) -> None:
    try:
        raise ExceptionGroup("tasks failed", [_statement_error_from_codec(), KeyError("kept")])
    except ExceptionGroup:
        get_logger("app.test").exception("tasks_failed")

    (line,) = logs.lines()
    assert "Pers0nalValue" not in line["exception"]
    assert f"StatementError: {_WITHHELD}" in line["exception"]
    assert "KeyError: 'kept'" in line["exception"]  # other members keep their message


def test_quoted_values_in_plain_and_nested_strings_are_redacted(logs: LogReader) -> None:
    get_logger("app.test").error(_ASYNCPG_MESSAGE, ctx={"items": [(_ASYNCPG_MESSAGE,)]})

    (line,) = logs.lines()
    assert line["event"] == "invalid input for query argument $1: [REDACTED]"
    assert line["ctx"] == {"items": [["invalid input for query argument $1: [REDACTED]"]]}


def test_deeply_nested_values_are_replaced_not_rendered(logs: LogReader) -> None:
    nested: list[object] = [_ASYNCPG_MESSAGE]
    for _ in range(20):
        nested = [nested]

    get_logger("app.test").error("deep", nested=nested)

    (line,) = logs.lines()
    assert "Pers0nalValue" not in str(line["nested"])


def test_access_log_stays_off_when_a_handler_is_attached_again(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", "json")
    access = logging.getLogger("uvicorn.access")
    handler = logging.StreamHandler()  # uvicorn's own config re-attaching its handler
    access.addHandler(handler)
    try:
        access.warning('127.0.0.1 - "GET /x?token=secret HTTP/1.1" 500')
    finally:
        access.removeHandler(handler)

    captured = capsys.readouterr()
    assert "token=secret" not in captured.out + captured.err


def test_uvicorn_loggers_follow_log_level(logs: LogReader) -> None:
    # uvicorn's dictConfig (or --log-level) pins its loggers to INFO before the factory.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(logging.INFO)
    configure_logging("WARNING", "json")

    logging.getLogger("uvicorn.error").info("Started server process")
    logging.getLogger("uvicorn").warning("kept")

    assert [line["event"] for line in logs.lines()] == ["kept"]
