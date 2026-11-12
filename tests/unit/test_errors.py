"""Problem-details rendering in app.core.errors."""

import json
from typing import ClassVar

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.errors import (
    AppError,
    ProblemDetails,
    problem_openapi_response,
    problem_response,
    problem_type,
)
from app.core.errors.handlers import handle_app_error, handle_http_exception


class _ConflictError(AppError):
    status: ClassVar[int] = 409
    code: ClassVar[str] = "slot_already_taken"
    title: ClassVar[str] = "Slot already taken"


def _body(raw: bytes | memoryview) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(bytes(raw))
    return loaded


def _request(headers: dict[str, str], state: dict[str, object] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "state": state or {},
    }
    return Request(scope)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("not_ready", "/problems/not-ready"), ("slot_already_taken", "/problems/slot-already-taken")],
)
def test_problem_type_maps_snake_case_code_to_kebab_uri(code: str, expected: str) -> None:
    assert problem_type(code) == expected


def test_extensions_cannot_override_standard_members() -> None:
    error = _ConflictError(
        "Taken.",
        extensions={
            "status": 200,
            "code": "ok",
            "title": "x",
            "type": "y",
            "detail": "z",
            "request_id": "forged",
            "slot": 7,
        },
    )

    response = problem_response(error, "req-1")

    assert response.status_code == 409
    assert response.media_type == "application/problem+json"
    assert response.headers["x-request-id"] == "req-1"
    assert _body(response.body) == {
        "type": "/problems/slot-already-taken",
        "title": "Slot already taken",
        "status": 409,
        "detail": "Taken.",
        "code": "slot_already_taken",
        "request_id": "req-1",
        "slot": 7,
    }


async def test_handler_prefers_request_id_stored_on_request_state() -> None:
    request = _request({"X-Request-ID": "from-header"}, {"request_id": "from-state"})

    response = await handle_app_error(request, _ConflictError("Taken."))

    assert response.headers["x-request-id"] == "from-state"
    assert _body(response.body)["request_id"] == "from-state"


async def test_handler_falls_back_to_the_incoming_header() -> None:
    response = await handle_app_error(_request({"X-Request-ID": "abc-1"}), _ConflictError("x"))

    assert response.headers["x-request-id"] == "abc-1"
    assert _body(response.body)["request_id"] == "abc-1"


def test_problem_openapi_response_documents_problem_media_type() -> None:
    described = problem_openapi_response(ProblemDetails, "A problem.", {"code": "x"})

    assert described["model"] is ProblemDetails
    assert described["description"] == "A problem."
    assert described["content"] == {"application/problem+json": {"example": {"code": "x"}}}


@pytest.mark.parametrize("detail", [{"field": "x"}, ["a", "b"], 7, ""])
async def test_non_string_or_empty_http_detail_becomes_the_status_phrase(detail: object) -> None:
    error = HTTPException(400, detail=detail)

    response = await handle_http_exception(_request({"X-Request-ID": "r"}), error)

    assert _body(response.body)["detail"] == "Bad Request"


async def test_string_http_detail_is_kept() -> None:
    error = HTTPException(400, detail="Cursor is malformed.")

    response = await handle_http_exception(_request({"X-Request-ID": "r"}), error)

    assert _body(response.body)["detail"] == "Cursor is malformed."
