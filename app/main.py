"""Application factory.

Run with: uvicorn app.main:create_app --factory --no-access-log
(`--no-access-log`: the request-context middleware logs one `http_request` event per
request; uvicorn's access logger is also disabled in app.core.logging if the flag is
forgotten.)
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.types import ASGIApp

from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_session_factory
from app.core.errors import document_problems, register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import create_redis_client
from app.modules.health.repository import HealthRepository
from app.modules.health.router import router as health_router
from app.modules.health.service import ReadinessService

API_TITLE = "DTM Tutor API"
API_VERSION = "0.1.0"


class _Application(FastAPI):
    """FastAPI with the request-context middleware outermost and problem-aware OpenAPI.

    `add_middleware` would place the middleware inside Starlette's
    ServerErrorMiddleware, where it never sees the 500 response; wrapping the built
    stack puts it outermost.
    """

    def build_middleware_stack(self) -> ASGIApp:
        return RequestContextMiddleware(super().build_middleware_stack())

    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            document_problems(super().openapi())  # generated once, then cached
        return super().openapi()


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
    configure_logging(resolved.log_level, resolved.log_format)
    app = _Application(title=API_TITLE, version=API_VERSION, lifespan=_build_lifespan(resolved))
    register_error_handlers(app)
    app.include_router(health_router)
    return app
