"""Application errors rendered as RFC 9457 problem details.

`AppError` is the single base for expected failures raised by application code;
`register_error_handlers` renders it and every framework error as a problem.
"""

from app.core.errors.handlers import register_error_handlers
from app.core.errors.openapi import document_problems, problem_openapi_response
from app.core.errors.problem import (
    PROBLEM_MEDIA_TYPE,
    AppError,
    Problem,
    ProblemDetails,
    ValidationErrorItem,
    ValidationProblem,
    problem_response,
    problem_type,
    render_problem,
)

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "AppError",
    "Problem",
    "ProblemDetails",
    "ValidationErrorItem",
    "ValidationProblem",
    "document_problems",
    "problem_openapi_response",
    "problem_response",
    "problem_type",
    "register_error_handlers",
    "render_problem",
]
