"""Database values in an error never reach the log, whichever side quotes them.

asyncpg quotes a value it rejects client-side ("query argument $1: '...'"); PostgreSQL
quotes a value it cannot cast ('invalid input syntax for type integer: "..."') and
repeats the row's values in the DETAIL line of a constraint violation. Every case is
raised against the real server and logged in both formats.
"""

import json
import traceback

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import LogFormat, Settings
from app.core.db import create_engine
from app.core.logging import configure_logging, get_logger

_PERSONAL = "Pers0nal-Value-7"
_FORMATS = ["json", "console"]

# Rolled back with the test's transaction: nothing is left on the shared database.
_SCHEMA = (
    "CREATE TYPE redaction_mood AS ENUM ('calm')",
    "CREATE TABLE redaction_user (email text PRIMARY KEY)",
    "CREATE TABLE redaction_person (age integer NOT NULL CHECK (age > 0), name text)",
    "CREATE TABLE redaction_account (email text REFERENCES redaction_user (email))",
)

# Each case: the statement, the value it quotes, and the expected sqlstate,
# constraint name and column name of the server error.
_SERVER_CASES = {
    "unique": (
        "INSERT INTO redaction_user (email) VALUES (:v)",
        "unique-secret@example.com",
        "23505",
        "redaction_user_pkey",
        None,
    ),
    "check": (
        "INSERT INTO redaction_person (age, name) VALUES (-1, :v)",
        "Check-Secret-Name",
        "23514",
        "redaction_person_age_check",
        None,
    ),
    "not_null": (
        "INSERT INTO redaction_person (age, name) VALUES (NULL, :v)",
        "NotNull-Secret-Name",
        "23502",
        None,
        "age",
    ),
    "foreign_key": (
        "INSERT INTO redaction_account (email) VALUES (:v)",
        "fk-secret@example.com",
        "23503",
        "redaction_account_email_fkey",
        None,
    ),
    "cast_integer": (
        "SELECT CAST(CAST(:v AS text) AS integer)",
        "Int-Secret-3",
        "22P02",
        None,
        None,
    ),
    "cast_uuid": ("SELECT CAST(CAST(:v AS text) AS uuid)", "Uuid-Secret-4", "22P02", None, None),
    "cast_enum": (
        "SELECT CAST(CAST(:v AS text) AS redaction_mood)",
        "Enum-Secret-5",
        "22P02",
        None,
        None,
    ),
    "integer_out_of_range": (
        "SELECT CAST(CAST(:v AS text) AS integer)",
        "98765432109876",
        "22003",
        None,
        None,
    ),
    "date_out_of_range": (
        "SELECT CAST(CAST(:v AS text) AS date)",
        "1999-13-31",
        "22008",
        None,
        None,
    ),
    "malformed_array": (
        "SELECT CAST(CAST(:v AS text) AS integer[])",
        "{Array-Secret-6",
        "22P02",
        None,
        None,
    ),
    "jsonb_token": (
        "SELECT CAST(CAST(:v AS text) AS jsonb)",
        "{JsonSecret8",
        "22P02",
        None,
        None,
    ),
}


async def _raise_from_database(settings: Settings, statement: str, value: str) -> DBAPIError:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                for ddl in _SCHEMA:
                    await connection.execute(text(ddl))
                await connection.execute(
                    text("INSERT INTO redaction_user (email) VALUES ('unique-secret@example.com')")
                )
                with pytest.raises(DBAPIError) as raised:
                    async with connection.begin_nested():
                        await connection.execute(text(statement), {"v": value})
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
    return raised.value


def _log_exception(error: DBAPIError) -> None:
    try:
        raise error
    except DBAPIError:
        get_logger("app.test").exception("query_failed")


@pytest.mark.parametrize("log_format", _FORMATS)
async def test_rejected_bound_value_never_reaches_the_log(
    test_settings: Settings, capsys: pytest.CaptureFixture[str], log_format: LogFormat
) -> None:
    configure_logging("INFO", log_format)
    error = await _raise_from_database(test_settings, "SELECT CAST(:v AS uuid)", _PERSONAL)
    # The premise: hide_parameters does not keep asyncpg's own message clean.
    assert f"query argument $1: '{_PERSONAL}'" in str(error)

    _log_exception(error)

    out = capsys.readouterr().out
    assert "query argument $1: [REDACTED]" in out
    assert _PERSONAL not in out


@pytest.mark.parametrize("log_format", _FORMATS)
@pytest.mark.parametrize("case", sorted(_SERVER_CASES))
async def test_server_quoted_value_never_reaches_the_log(
    test_settings: Settings, capsys: pytest.CaptureFixture[str], log_format: LogFormat, case: str
) -> None:
    statement, value, _sqlstate, _constraint, _column = _SERVER_CASES[case]
    configure_logging("INFO", log_format)
    error = await _raise_from_database(test_settings, statement, value)
    # The premise: the traceback (the message or its DETAIL line) carries the value.
    assert value.strip("{") in "".join(traceback.format_exception(error))

    _log_exception(error)

    out = capsys.readouterr().out
    assert "[REDACTED]" in out
    assert value.strip("{") not in out


@pytest.mark.parametrize("case", sorted(_SERVER_CASES))
async def test_server_error_identifiers_are_logged_without_values(
    test_settings: Settings, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    statement, value, sqlstate, constraint, column = _SERVER_CASES[case]
    configure_logging("INFO", "json")
    error = await _raise_from_database(test_settings, statement, value)

    _log_exception(error)

    (line,) = capsys.readouterr().out.splitlines()
    db_error = json.loads(line)["db_error"]
    assert db_error["sqlstate"] == sqlstate
    assert db_error.get("constraint_name") == constraint
    assert db_error.get("column_name") == column


def test_a_value_quoted_in_a_plain_message_is_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """str(error) passed as a field (no exc_info) is redacted like a traceback."""
    configure_logging("INFO", "json")
    message = 'invalid input syntax for type integer: "a"b-Secret"\nDETAIL:  Key (x)=(Row-Secret)'

    get_logger("app.test").error("query_failed", error=message)

    (line,) = capsys.readouterr().out.splitlines()
    error = json.loads(line)["error"]
    assert error == "invalid input syntax for type integer: [REDACTED]\nDETAIL: [REDACTED]"
