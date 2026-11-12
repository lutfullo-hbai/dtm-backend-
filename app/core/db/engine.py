"""Engine and session factory. Both are built in the application lifespan."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.db.slow_query import install_slow_query_logging


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine. No connection is opened until first use."""
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
        },
    )
    install_slow_query_logging(engine.sync_engine, threshold_ms=settings.slow_query_ms)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory for the engine.

    `expire_on_commit=False`: after a commit, attributes stay readable instead of
    being expired, because reloading them would be implicit I/O, which async
    sessions cannot do.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
