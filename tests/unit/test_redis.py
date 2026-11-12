from pydantic import SecretStr
from redis.asyncio import BlockingConnectionPool

from app.core.config import Settings
from app.core.redis import create_redis_client


async def test_client_uses_a_bounded_blocking_pool_with_bounded_timeouts() -> None:
    settings = Settings(
        app_env="test",
        database_url=SecretStr("postgresql+asyncpg://dtm:dtm@localhost:5432/dtm"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        redis_socket_timeout_seconds=1.5,
        redis_max_connections=7,
    )

    client = create_redis_client(settings)
    try:
        pool = client.connection_pool
        assert isinstance(pool, BlockingConnectionPool)
        assert pool.max_connections == 7
        assert pool.timeout == 1.5  # waiting for a free connection is bounded too
        assert pool.connection_kwargs["socket_connect_timeout"] == 1.5
        assert pool.connection_kwargs["socket_timeout"] == 1.5
    finally:
        await client.aclose()
