"""RFC 9457 problem details: the wire models, `AppError` and the renderer.

Every error response of the API is `application/problem+json` with the members
type, title, status, detail, code and request_id, plus optional extension members
(`errors` for validation, `checks` for readiness).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.request_id import REQUEST_ID_HEADER

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemDetails(BaseModel):
    """RFC 9457 problem body shared by every error response."""

    type: str = Field(description="URI reference identifying the problem type.")
    title: str = Field(description="Short, human-readable summary of the problem type.")
    status: int = Field(description="HTTP status code.")
    detail: str = Field(description="Human-readable explanation of this occurrence.")
    code: str = Field(description="Stable, machine-readable snake_case error key.")
    request_id: str = Field(description="Correlation id, also sent as X-Request-ID.")


class ValidationErrorItem(BaseModel):
    """One invalid input. The rejected value itself is never echoed back."""

    loc: list[str | int] = Field(description='Path to the input, e.g. ["query", "limit"].')
    message: str = Field(description="Human-readable reason.")
    type: str = Field(description="Stable machine-readable error type, e.g. `int_parsing`.")


class ValidationProblem(ProblemDetails):
    """Problem body for 422 `validation_error`."""

    errors: list[ValidationErrorItem] = Field(description="Every invalid input.")


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


@dataclass(frozen=True, slots=True, kw_only=True)
class Problem:
    """One occurrence of a problem: the RFC 9457 members other than `type`.

    `type` is derived from `code`; `request_id` is added when the response is built.
    """

    status: int
    code: str
    title: str
    detail: str
    extensions: Mapping[str, object] = field(default_factory=dict)


def render_problem(
    problem: Problem, request_id: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Build a problem+json response; extensions never override the standard members."""
    body: dict[str, object] = {
        "type": problem_type(problem.code),
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "code": problem.code,
        "request_id": request_id,
    }
    for name, value in problem.extensions.items():
        body.setdefault(name, value)
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def problem_response(error: AppError, request_id: str) -> JSONResponse:
    """Render an `AppError` as a problem+json response."""
    problem = Problem(
        status=error.status,
        code=error.code,
        title=error.title,
        detail=error.detail,
        extensions=error.extensions,
    )
    return render_problem(problem, request_id)
