"""Request id and access logging by the request-context middleware, through the app."""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.main import create_app
from app.modules.health.router import get_readiness_service
from app.modules.health.service import ReadinessService
from tests.support.logs import LogReader

_SETTINGS = Settings(
    app_env="test",
    database_url=SecretStr("postgresql+asyncpg://dtm:dtm@127.0.0.1:1/dtm"),
    redis_url=SecretStr("redis://127.0.0.1:1/0"),
)
_SECRET = "Bearer s3cr3t-token-value"


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError


def _app() -> FastAPI:
    app = create_app(_SETTINGS)

    async def echo(number: int) -> dict[str, int]:
        get_logger("app.test").info("inside_endpoint")
        return {"number": number}

    async def boom() -> dict[str, str]:
        msg = "internal detail that must not leak"
        raise RuntimeError(msg)

    app.add_api_route("/echo/{number}", echo, methods=["GET"])
    app.add_api_route("/boom", boom, methods=["GET"])
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/echo/1", 200),
        ("GET", "/nope", 404),
        ("POST", "/health", 405),
        ("GET", "/echo/not-a-number", 422),
        ("GET", "/boom", 500),
        ("GET", "/ready", 503),
    ],
)
async def test_every_response_carries_the_request_id(
    logs: LogReader, method: str, path: str, status: int
) -> None:
    app = _app()
    app.dependency_overrides[get_readiness_service] = lambda: ReadinessService(_ok, _down, 0.5)

    async with _client(app) as client:
        reused = await client.request(method, path, headers={"X-Request-ID": "given-id.1"})
        minted = await client.request(method, path)

    assert reused.status_code == status
    assert reused.headers["x-request-id"] == "given-id.1"
    assert minted.headers["x-request-id"]
    assert minted.headers["x-request-id"] != "given-id.1"
    if status >= 400:
        assert reused.headers["content-type"] == "application/problem+json"
        assert reused.json()["request_id"] == "given-id.1"
        assert minted.json()["request_id"] == minted.headers["x-request-id"]
    access = logs.events("http_request")
    assert [line["request_id"] for line in access] == ["given-id.1", minted.headers["x-request-id"]]
    assert {line["status"] for line in access} == {status}


async def test_a_successful_liveness_probe_is_logged_at_debug_only(logs: LogReader) -> None:
    async with _client(_app()) as client:
        response = await client.get("/health")
        assert logs.events("http_request") == []  # nothing at INFO

        configure_logging("DEBUG", "json")
        await client.get("/health")

    assert response.status_code == 200
    (access,) = logs.events("http_request")
    assert access["level"] == "debug"
    assert access["path"] == "/health"


async def test_one_http_request_event_per_request_with_its_fields(logs: LogReader) -> None:
    async with _client(_app()) as client:
        await client.get("/echo/5?token=query-secret", headers={"Authorization": _SECRET})

    (access,) = logs.events("http_request")
    assert access["level"] == "info"
    assert access["logger"] == "app.http"
    assert access["method"] == "GET"
    assert access["path"] == "/echo/5"
    assert access["status"] == 200
    assert isinstance(access["duration_ms"], float)
    logged = repr(logs.lines())
    assert "query-secret" not in logged
    assert "s3cr3t-token-value" not in logged


async def test_lines_logged_while_serving_carry_the_request_id(logs: LogReader) -> None:
    async with _client(_app()) as client:
        response = await client.get("/echo/5")

    (inside,) = logs.events("inside_endpoint")
    assert inside["request_id"] == response.headers["x-request-id"]


async def test_unhandled_exception_traceback_is_logged_once_with_the_request_id(
    logs: LogReader,
) -> None:
    async with _client(_app()) as client:
        response = await client.get("/boom", headers={"X-Request-ID": "boom-1"})

    assert response.status_code == 500
    assert "internal detail" not in response.text
    (error,) = logs.events("unhandled_exception")
    assert error["level"] == "error"
    assert error["request_id"] == "boom-1"
    assert "RuntimeError: internal detail that must not leak" in error["exception"]
    (access,) = logs.events("http_request")
    assert access["level"] == "error"
    assert access["status"] == 500
