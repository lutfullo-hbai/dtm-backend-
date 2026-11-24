"""Per-test database isolation on the shared container, and an HTTP client for the real app.

Every database access a test can cause (the test's own session, a request's session
from the real `get_session`, and any session the app opens from
`app.state.session_factory`) runs on one connection inside one outer transaction that
is rolled back after the test. No dependency is overridden: `get_session` itself opens
the request's session from `app.state.session_factory`, which the `app` fixture points
at `test_session_factory`. Sessions join the outer transaction with
`join_transaction_mode="create_savepoint"`: code under test may commit or roll back
freely; that only ends a savepoint.

One connection means one statement at a time: a test must not run two sessions'
queries concurrently (asyncpg would refuse).
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from app.core.config import Settings
from app.core.db import AsyncSession, create_engine
from app.main import create_app


@pytest.fixture
async def db_connection(test_settings: Settings) -> AsyncIterator[AsyncConnection]:
    """A connection inside an outer transaction that is rolled back after the test."""
    engine = create_engine(test_settings)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def test_session_factory(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """Like the app's factory, but every session joins the test's outer transaction."""
    return async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
async def db_session(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """The test's own session: a request's session is another one on the same connection."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture
async def app(
    test_settings: Settings, test_session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[FastAPI]:
    """The real application, lifespan running against the containers.

    The lifespan is entered directly (`router.lifespan_context`), so no extra
    dependency is needed. Afterwards the session factory, the one way the app reaches
    the database (the real `get_session` included), is redirected into the test
    transaction.
    """
    application = create_app(test_settings)
    async with application.router.lifespan_context(application):
        application.state.session_factory = test_session_factory
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
