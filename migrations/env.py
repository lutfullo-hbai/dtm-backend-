"""Alembic environment: async engine, URL from the application's Settings.

Models are discovered through `Base.metadata`; each feature module's `models` must
be imported below once it exists, or autogenerate will not see its tables.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.core.db import Base

config = context.config
# Tests drive migrations with their own logging; only the CLI applies alembic.ini's.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

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
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
