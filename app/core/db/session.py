"""Request-scoped database session."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one `AsyncSession` per request.

    Nothing is committed implicitly: the service owning the unit of work commits.
    If the request fails, the transaction is rolled back; the session is always
    closed, which also discards any work that was not committed.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
