"""Application lifespan: every resource built is released, also on partial failure."""

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from app.modules.health.service import ReadinessService

# Nothing connects: the engine and Redis client open connections only on first use.
_SETTINGS = Settings(
    app_env="test",
    database_url=SecretStr("postgresql+asyncpg://dtm:dtm@127.0.0.1:1/dtm"),
    redis_url=SecretStr("redis://127.0.0.1:1/0"),
)


@pytest.fixture
def disposed(monkeypatch: pytest.MonkeyPatch) -> list[AsyncEngine]:
    """Engines whose (real) `dispose` was awaited."""
    calls: list[AsyncEngine] = []
    original = AsyncEngine.dispose

    async def spy(self: AsyncEngine, close: bool = True) -> None:  # noqa: FBT001, FBT002  # mirrors AsyncEngine.dispose
        calls.append(self)
        await original(self, close)

    monkeypatch.setattr(AsyncEngine, "dispose", spy)
    return calls


async def test_engine_is_disposed_when_redis_client_creation_fails(
    monkeypatch: pytest.MonkeyPatch, disposed: list[AsyncEngine]
) -> None:
    def broken_redis_client(_settings: Settings) -> None:
        msg = "redis client could not be built"
        raise RuntimeError(msg)

    monkeypatch.setattr("app.main.create_redis_client", broken_redis_client)
    app = create_app(_SETTINGS)

    with pytest.raises(RuntimeError, match="redis client could not be built"):
        async with app.router.lifespan_context(app):
            pytest.fail("the lifespan must not start")

    assert len(disposed) == 1
    assert not hasattr(app.state, "readiness_service")


async def test_engine_is_disposed_once_on_normal_shutdown(disposed: list[AsyncEngine]) -> None:
    app = create_app(_SETTINGS)

    async with app.router.lifespan_context(app):
        assert disposed == []

    assert len(disposed) == 1


async def test_readiness_probes_are_drained_before_the_engine_is_disposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    original_dispose = AsyncEngine.dispose

    async def record_dispose(self: AsyncEngine, close: bool = True) -> None:  # noqa: FBT001, FBT002  # mirrors AsyncEngine.dispose
        closed.append("engine")
        await original_dispose(self, close)

    async def record_aclose(_self: ReadinessService) -> None:
        closed.append("readiness")

    monkeypatch.setattr(AsyncEngine, "dispose", record_dispose)
    monkeypatch.setattr(ReadinessService, "aclose", record_aclose)
    app = create_app(_SETTINGS)

    async with app.router.lifespan_context(app):
        assert closed == []

    assert closed == ["readiness", "engine"]
