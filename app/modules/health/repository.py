"""Dependency probes for readiness: one round-trip each, no interpretation."""

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class HealthRepository:
    """Reaches PostgreSQL and Redis with the cheapest possible round-trip."""

    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self._engine = engine
        self._redis = redis

    async def ping_database(self) -> None:
        """Run `SELECT 1` on a pooled connection; raises on any failure."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def ping_redis(self) -> None:
        """Send `PING`; raises on any failure."""
        await self._redis.ping()
