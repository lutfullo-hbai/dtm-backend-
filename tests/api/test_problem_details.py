"""Every error the application produces is an RFC 9457 problem (app.core.errors)."""

from typing import Annotated, Any

import httpx
import pytest
from fastapi import FastAPI, Query
from pydantic import SecretStr
from starlette.exceptions import HTTPException

from app.core.config import Settings
from app.main import create_app
from tests.support.logs import LogReader

_SETTINGS = Settings(
    app_env="test",
    database_url=SecretStr("postgresql+asyncpg://dtm:dtm@127.0.0.1:1/dtm"),
    redis_url=SecretStr("redis://127.0.0.1:1/0"),
)
_SECRET_INPUT = "Leak3dInputMarker"
_PROBLEM = "application/problem+json"


def _app() -> FastAPI:
    app = create_app(_SETTINGS)

    async def items(limit: Annotated[int, Query(le=100)]) -> dict[str, int]:
        return {"limit": limit}

    async def boom() -> dict[str, str]:
        msg = f"database password is {_SECRET_INPUT}"
        raise RuntimeError(msg)

    async def unauthorized() -> dict[str, str]:
        raise HTTPException(401, "Token missing.", headers={"WWW-Authenticate": "Bearer"})

    async def odd_status() -> dict[str, str]:
        raise HTTPException(599)

    async def not_modified() -> dict[str, str]:
        raise HTTPException(304, headers={"ETag": '"v1"'})

    app.add_api_route("/items", items, methods=["GET"])
    app.add_api_route("/boom", boom, methods=["GET"])
    app.add_api_route("/unauthorized", unauthorized, methods=["GET"])
    app.add_api_route("/odd", odd_status, methods=["GET"])
    app.add_api_route("/not-modified", not_modified, methods=["GET"])
    return app


async def _request(method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers={"X-Request-ID": "rid-1"})


def _assert_problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    assert response.status_code == status
    assert response.headers["content-type"] == _PROBLEM
    assert response.headers["x-request-id"] == body["request_id"] == "rid-1"
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == "/problems/" + code.replace("_", "-")
    assert body["title"]
    assert body["detail"]
    return body


async def test_unknown_route_is_not_found() -> None:
    body = _assert_problem(await _request("GET", "/nope"), 404, "not_found")

    assert body["title"] == "Not Found"


async def test_trailing_slash_is_not_found_and_never_redirected() -> None:
    """Starlette would redirect to http://<Host header>/health: an open redirect."""
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health/", headers={"Host": "evil.example", "X-Request-ID": "rid-1"}
        )

    _assert_problem(response, 404, "not_found")
    assert "location" not in response.headers


async def test_wrong_method_is_method_not_allowed_and_keeps_allow() -> None:
    response = await _request("POST", "/health")

    _assert_problem(response, 405, "method_not_allowed")
    assert response.headers["allow"] == "GET"


async def test_validation_error_lists_errors_without_echoing_input() -> None:
    response = await _request("GET", f"/items?limit={_SECRET_INPUT}")

    body = _assert_problem(response, 422, "validation_error")
    assert body["errors"] == [
        {
            "loc": ["query", "limit"],
            "message": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    assert _SECRET_INPUT not in response.text
    assert set(body) == {"type", "title", "status", "detail", "code", "request_id", "errors"}


async def test_missing_parameter_is_a_validation_error() -> None:
    body = _assert_problem(await _request("GET", "/items"), 422, "validation_error")

    assert body["errors"][0]["type"] == "missing"


async def test_unhandled_exception_is_generic_and_logged_only(logs: LogReader) -> None:
    response = await _request("GET", "/boom")

    body = _assert_problem(response, 500, "internal_error")
    assert body["title"] == "Internal Server Error"
    assert _SECRET_INPUT not in response.text
    assert "Traceback" not in response.text
    (error,) = logs.events("unhandled_exception")
    assert error["request_id"] == "rid-1"
    assert "Traceback" in error["exception"]
    assert f"RuntimeError: database password is {_SECRET_INPUT}" in error["exception"]


async def test_other_http_exception_keeps_status_detail_and_headers() -> None:
    response = await _request("GET", "/unauthorized")

    body = _assert_problem(response, 401, "http_401")
    assert body["title"] == "Unauthorized"
    assert body["detail"] == "Token missing."
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unregistered_status_gets_a_generic_title() -> None:
    body = _assert_problem(await _request("GET", "/odd"), 599, "http_599")

    assert body["title"] == "HTTP Error"


async def test_bodiless_status_stays_bodiless() -> None:
    response = await _request("GET", "/not-modified")

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == '"v1"'
    assert response.headers["x-request-id"] == "rid-1"


@pytest.fixture
def openapi() -> dict[str, Any]:
    return _app().openapi()


def test_every_operation_documents_the_internal_error(openapi: dict[str, Any]) -> None:
    for path_item in openapi["paths"].values():
        for operation in path_item.values():
            content = operation["responses"]["500"]["content"]
            assert content[_PROBLEM]["schema"] == {"$ref": "#/components/schemas/ProblemDetails"}


def test_validation_response_is_the_validation_problem(openapi: dict[str, Any]) -> None:
    response = openapi["paths"]["/items"]["get"]["responses"]["422"]

    assert list(response["content"]) == [_PROBLEM]
    content = response["content"][_PROBLEM]
    assert content["schema"] == {"$ref": "#/components/schemas/ValidationProblem"}
    assert content["example"]["code"] == "validation_error"
    schemas = openapi["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert schemas["ValidationProblem"]["properties"]["errors"]["items"] == {
        "$ref": "#/components/schemas/ValidationErrorItem"
    }


def test_not_ready_problem_requires_both_checks(openapi: dict[str, Any]) -> None:
    response = openapi["paths"]["/ready"]["get"]["responses"]["503"]

    assert list(response["content"]) == [_PROBLEM]
    assert response["content"][_PROBLEM]["schema"] == {
        "$ref": "#/components/schemas/NotReadyProblem"
    }
    checks = openapi["components"]["schemas"]["ReadinessChecks"]
    assert set(checks["required"]) == {"database", "redis"}
