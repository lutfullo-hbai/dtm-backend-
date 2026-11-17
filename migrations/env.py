"""Alembic environment: async engine, URL from the application's Settings.

Models are discovered through `Base.metadata`; each feature module's `models` must
be imported below once it exists, or autogenerate will not see its tables.
"""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.core.db import Base
from app.core.logging import configure_logging, get_logger

config = context.config
logger = get_logger("migrations")
# Tests drive migrations with their own logging; only the CLI configures it. Online
# runs log like the application (structured, stdout). Offline runs (`--sql`) print the
# SQL itself to stdout, so their log lines go to stderr as alembic.ini describes.
_from_cli = False
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    _from_cli = True
    if context.is_offline_mode():
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    else:
        settings = get_settings()
        configure_logging(settings.log_level, settings.log_format)

target_metadata = Base.metadata


def _database_url() -> str:
    """An explicit URL (tests) wins; otherwise DATABASE_URL, validated by Settings."""
    explicit = config.attributes.get("database_url")
    if isinstance(explicit, str):
        return explicit
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without connecting."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)


async def _run_with_own_engine() -> None:
    # NullPool: a one-off process holds one connection and must not keep it.
    # hide_parameters: a data migration's bound values stay out of error messages.
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        await _run_async_migrations(engine)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run against a database.

    pytest-alembic hands in its own engine as `config.attributes["connection"]`; it
    stays the caller's to dispose. Otherwise an engine is built from DATABASE_URL.
    """
    handed_in = config.attributes.get("connection")
    if isinstance(handed_in, AsyncEngine):
        asyncio.run(_run_async_migrations(handed_in))
    else:
        asyncio.run(_run_with_own_engine())


def run_migrations_online_from_cli() -> None:
    """Run online; a failure is logged through the structured pipeline, then exit 1.

    Uncaught, the error would reach the interpreter's default excepthook, which prints
    the raw message: a constraint violation's DETAIL repeats the row's values. Logged
    with `logger.exception`, the database error's message is withheld
    (app.core.log_redaction) and only its type, frames and identifiers are written.
    """
    try:
        run_migrations_online()
    except Exception:
        logger.exception("migration_failed")
        sys.exit(1)


if context.is_offline_mode():
    run_migrations_offline()
elif _from_cli:
    run_migrations_online_from_cli()
else:
    run_migrations_online()
