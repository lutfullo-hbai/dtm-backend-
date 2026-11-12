"""Redis client lifecycle."""

from redis.asyncio import BlockingConnectionPool, Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """Create the Redis client. No connection is opened until first use.

    Connect and per-command socket timeouts are both bounded, so a hung Redis
    cannot stall a request indefinitely. The pool is bounded and blocking: when
    REDIS_MAX_CONNECTIONS are all busy a caller waits for a free connection, at
    most REDIS_SOCKET_TIMEOUT_SECONDS, instead of failing immediately.
    """
    timeout = settings.redis_socket_timeout_seconds
    pool = BlockingConnectionPool.from_url(
        settings.redis_url.get_secret_value(),
        max_connections=settings.redis_max_connections,
        timeout=timeout,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    # from_pool hands pool ownership to the client: aclose() also closes the pool.
    return Redis.from_pool(pool)
