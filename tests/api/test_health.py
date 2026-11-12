"""API tests for /health and /ready that need no running PostgreSQL or Redis.

The ready-path against real PostgreSQL and Redis is in
tests/integration/test_readiness.py.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.modules.health.router import get_readiness_service
from app.modules.health.service import Probe, ReadinessService

# Port 1 on loopback is closed, so connections are refused immediately.
_REFUSED_DATABASE = "postgresql+asyncpg://dtm:dtm@127.0.0.1:1/dtm"
_REFUSED_REDIS = "redis://127.0.0.1:1/0"
_TIMEOUT_SECONDS = 0.5


def _settings(database_url: str, redis_url: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        readiness_timeout_seconds=_TIMEOUT_SECONDS,
    )


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def silent_port() -> AsyncIterator[int]:
    """A TCP port that accepts connections but never answers (simulates a hung dependency)."""
    held: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    yield port
    for writer in held:
        writer.close()
    server.close()
    await server.wait_closed()


class _SpyReadinessService:
    """Stands in for the readiness service and fails the test on any use."""

    def __getattr__(self, name: str) -> object:
        pytest.fail(f"/health touched the readiness service: {name}")


async def test_health_makes_no_dependency_calls() -> None:
    # No lifespan, so no engine or Redis client exists; the only thing /health could
    # reach is app.state, and the spy there fails on any attribute access.
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))
    app.state.readiness_service = _SpyReadinessService()

    async with await _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


async def test_health_is_ok_while_dependencies_are_down() -> None:
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))

    async with app.router.lifespan_context(app), await _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_problem_when_dependencies_refuse() -> None:
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))

    async with app.router.lifespan_context(app), await _client(app) as client:
        response = await client.get("/ready", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-request-id"] == "req-123"
    assert response.json() == {
        "type": "/problems/not-ready",
        "title": "Service not ready",
        "status": 503,
        "detail": "One or more dependencies are unavailable.",
        "code": "not_ready",
        "request_id": "req-123",
        "checks": {"database": "unavailable", "redis": "unavailable"},
    }


async def test_ready_replaces_malformed_request_id() -> None:
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))

    async with app.router.lifespan_context(app), await _client(app) as client:
        response = await client.get("/ready", headers={"X-Request-ID": "bad id!"})

    request_id = response.json()["request_id"]
    assert request_id != "bad id!"
    assert response.headers["x-request-id"] == request_id


async def test_ready_waits_one_timeout_not_two_when_dependencies_hang(silent_port: int) -> None:
    app = create_app(
        _settings(
            f"postgresql+asyncpg://dtm:dtm@127.0.0.1:{silent_port}/dtm",
            f"redis://127.0.0.1:{silent_port}/0",
        )
    )

    async with app.router.lifespan_context(app), await _client(app) as client:
        started = time.monotonic()
        response = await client.get("/ready")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "redis": "unavailable"}
    # The deadline is really waited for (not a fast refusal) ...
    assert elapsed >= _TIMEOUT_SECONDS * 0.9
    # ... and checks run concurrently: total time is about one timeout, not two.
    assert elapsed < _TIMEOUT_SECONDS * 1.9


async def _succeed() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError


def _override(database: Probe, redis: Probe) -> Callable[[], ReadinessService]:
    return lambda: ReadinessService(database, redis, _TIMEOUT_SECONDS)


async def test_ready_ok_when_all_checks_pass() -> None:
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))
    app.dependency_overrides[get_readiness_service] = _override(_succeed, _succeed)

    async with await _client(app) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_ready_reports_only_the_failing_dependency() -> None:
    app = create_app(_settings(_REFUSED_DATABASE, _REFUSED_REDIS))
    app.dependency_overrides[get_readiness_service] = _override(_succeed, _fail)

    async with await _client(app) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "unavailable"}
