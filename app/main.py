"""Application factory. Run with: uvicorn app.main:create_app --factory."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_session_factory
from app.core.errors import register_error_handlers
from app.core.redis import create_redis_client
from app.modules.health.repository import HealthRepository
from app.modules.health.router import router as health_router
from app.modules.health.service import ReadinessService

API_TITLE = "DTM Tutor API"
API_VERSION = "0.1.0"


def _build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Each resource is registered for cleanup as soon as it exists, so a failure
        # while building a later one still closes the earlier ones. Cleanup is LIFO:
        # readiness probes are drained before the clients they use are closed.
        async with AsyncExitStack() as stack:
            engine = create_engine(settings)
            stack.push_async_callback(engine.dispose)
            app.state.session_factory = create_session_factory(engine)
            redis = create_redis_client(settings)
            stack.push_async_callback(redis.aclose)
            repository = HealthRepository(engine, redis)
            readiness = ReadinessService(
                repository.ping_database,
                repository.ping_redis,
                settings.readiness_timeout_seconds,
            )
            stack.push_async_callback(readiness.aclose)
            app.state.readiness_service = readiness
            yield

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Settings are validated here, so bad config fails at startup."""
    resolved = settings if settings is not None else get_settings()
    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=_build_lifespan(resolved))
    register_error_handlers(app)
    app.include_router(health_router)
    return app
