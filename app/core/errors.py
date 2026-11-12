"""Application errors rendered as RFC 9457 problem details.

`AppError` is the single base for expected failures raised by application code.
This module renders it; handlers for validation errors, Starlette HTTP errors and
unhandled exceptions are added alongside it later (M0-08).
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.request_id import REQUEST_ID_HEADER, current_request_id

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemDetails(BaseModel):
    """RFC 9457 problem body shared by every error response."""

    type: str = Field(description="URI reference identifying the problem type.")
    title: str = Field(description="Short, human-readable summary of the problem type.")
    status: int = Field(description="HTTP status code.")
    detail: str = Field(description="Human-readable explanation of this occurrence.")
    code: str = Field(description="Stable, machine-readable snake_case error key.")
    request_id: str = Field(description="Correlation id, also sent as X-Request-ID.")


class AppError(Exception):
    """Base class for expected failures; subclasses set status, code and title."""

    status: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal Server Error"

    def __init__(self, detail: str, extensions: Mapping[str, object] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extensions: Mapping[str, object] = extensions or {}


def problem_type(code: str) -> str:
    """Map a stable error code to its problem type URI reference."""
    return "/problems/" + code.replace("_", "-")


def problem_response(error: AppError, request_id: str) -> JSONResponse:
    """Render an `AppError` as a problem+json response."""
    body: dict[str, object] = {
        "type": problem_type(error.code),
        "title": error.title,
        "status": error.status,
        "detail": error.detail,
        "code": error.code,
        "request_id": request_id,
    }
    for name, value in error.extensions.items():
        body.setdefault(name, value)  # extensions never override the standard members
    return JSONResponse(
        body,
        status_code=error.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={REQUEST_ID_HEADER: request_id},
    )


async def handle_app_error(request: Request, error: Exception) -> Response:
    """Exception handler for `AppError` and its subclasses."""
    if not isinstance(error, AppError):  # pragma: no cover - registered for AppError only
        raise error
    return problem_response(error, current_request_id(request))


def register_error_handlers(app: FastAPI) -> None:
    """Install the problem-details exception handlers on the application."""
    app.add_exception_handler(AppError, handle_app_error)


def problem_openapi_response(
    model: type[ProblemDetails],
    description: str,
    example: Mapping[str, object],
) -> dict[str, Any]:
    """Describe a problem response for an operation's `responses` mapping.

    FastAPI would document a `model` under the route's own media type
    (application/json), so the schema is inlined under application/problem+json
    instead. Problem models must therefore be flat (no nested models).
    """
    schema = model.model_json_schema()
    if "$defs" in schema:
        msg = f"{model.__name__} must not contain nested models"
        raise TypeError(msg)
    return {
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": schema, "example": dict(example)}},
    }
