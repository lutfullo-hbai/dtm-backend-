"""/health and /ready against real PostgreSQL and Redis containers."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import httpx
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.pool import Pool

from app.core.config import Settings
from app.main import create_app
from tests.support.query_counter import QueryCounter

# Port 1 on loopback is closed, so connections are refused immediately.
_UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"

AssertMaxQueries = Callable[[int], AbstractContextManager[QueryCounter]]


@contextmanager
def _count_pool_checkouts() -> Iterator[list[object]]:
    """Each checkout costs at most one pre-ping round-trip (pool_pre_ping=True)."""
    checkouts: list[object] = []

    def record(*_: object) -> None:
        checkouts.append(None)

    event.listen(Pool, "checkout", record)
    try:
        yield checkouts
    finally:
        event.remove(Pool, "checkout", record)


async def test_health_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_is_ok_against_real_services(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_ready_stays_within_its_database_budget(
    client: httpx.AsyncClient, assert_max_queries: AssertMaxQueries
) -> None:
    # Warm up: the first connection also runs SQLAlchemy's one-time dialect setup.
    await client.get("/ready")

    with _count_pool_checkouts() as checkouts, assert_max_queries(1) as queries:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert queries.statements == ["SELECT 1"]
    # Budget (contract section 7): SELECT 1 plus at most one pre-ping = 2 round-trips.
    assert queries.count + len(checkouts) <= 2


async def test_ready_is_503_when_redis_is_unreachable(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"redis_url": SecretStr(_UNREACHABLE_REDIS)})
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["checks"] == {"database": "ok", "redis": "unavailable"}
