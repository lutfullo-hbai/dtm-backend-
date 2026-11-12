"""Exception handlers: every error the application can produce becomes a problem.

| Situation                      | status      | code               |
|--------------------------------|-------------|--------------------|
| `AppError` subclass            | its status  | its code           |
| request validation             | 422         | validation_error   |
| unknown route                  | 404         | not_found          |
| method not allowed             | 405         | method_not_allowed |
| other Starlette HTTPException  | its status  | http_<status>      |
| unhandled exception            | 500         | internal_error     |

The request id comes from `request.state` (set by the request-context middleware),
so the body and the X-Request-ID header always match. The traceback of an unhandled
exception is logged by that middleware; the response says nothing about it.
"""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from app.core.errors.problem import AppError, Problem, problem_response, render_problem
from app.core.request_id import current_request_id

INTERNAL_ERROR_DETAIL = "An unexpected error occurred. Quote the request id when reporting it."
VALIDATION_DETAIL = "The request is invalid; see `errors`."
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
_BODILESS = frozenset({204, 304})


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "HTTP Error"


async def handle_app_error(request: Request, error: Exception) -> Response:
    """Render `AppError` and its subclasses."""
    if not isinstance(error, AppError):  # pragma: no cover - registered for AppError only
        raise error
    return problem_response(error, current_request_id(request))


async def handle_validation_error(request: Request, error: Exception) -> Response:
    """422 `validation_error`: location, message and type of each error; never the input."""
    if not isinstance(error, RequestValidationError):  # pragma: no cover - registered for it
        raise error
    errors = [
        {"loc": list(item["loc"]), "message": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]
    problem = Problem(
        status=422,
        code="validation_error",
        title="Validation failed",
        detail=VALIDATION_DETAIL,
        extensions={"errors": errors},
    )
    return render_problem(problem, current_request_id(request))


async def handle_http_exception(request: Request, error: Exception) -> Response:
    """Starlette/FastAPI HTTP errors, including the router's 404 and 405 (Allow kept)."""
    if not isinstance(error, HTTPException):  # pragma: no cover - registered for it
        raise error
    status = error.status_code
    if status in _BODILESS:
        return Response(status_code=status, headers=error.headers)
    phrase = _phrase(status)
    # Typed str by Starlette, but FastAPI's HTTPException accepts any JSON value; the
    # problem member is a string, so anything else (or an empty string) is the phrase.
    raw_detail: object = error.detail
    detail = raw_detail if isinstance(raw_detail, str) and raw_detail else phrase
    problem = Problem(
        status=status,
        code=_HTTP_CODES.get(status, f"http_{status}"),
        title=phrase,
        detail=detail,
    )
    return render_problem(problem, current_request_id(request), error.headers)


async def handle_unhandled_exception(request: Request, _error: Exception) -> Response:
    """500 `internal_error` with a generic detail; the cause stays in the log."""
    problem = Problem(
        status=500,
        code="internal_error",
        title="Internal Server Error",
        detail=INTERNAL_ERROR_DETAIL,
    )
    return render_problem(problem, current_request_id(request))


def register_error_handlers(app: FastAPI) -> None:
    """Install the problem-details exception handlers on the application."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unhandled_exception)
