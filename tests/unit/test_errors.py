"""Problem-details rendering in app.core.errors."""

import json
from typing import ClassVar

import pytest
from pydantic import BaseModel, create_model
from starlette.requests import Request

from app.core.errors import (
    AppError,
    ProblemDetails,
    handle_app_error,
    problem_openapi_response,
    problem_response,
    problem_type,
)


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


class _Inner(BaseModel):
    value: int


def test_problem_openapi_response_rejects_nested_models() -> None:
    nested = create_model("NestedProblem", __base__=ProblemDetails, inner=(_Inner, ...))

    with pytest.raises(TypeError, match="NestedProblem must not contain nested models"):
        problem_openapi_response(nested, "nested", {})


def test_problem_openapi_response_documents_problem_media_type() -> None:
    described = problem_openapi_response(ProblemDetails, "A problem.", {"code": "x"})

    content = described["content"]["application/problem+json"]
    assert described["description"] == "A problem."
    assert content["example"] == {"code": "x"}
    assert content["schema"]["title"] == "ProblemDetails"
