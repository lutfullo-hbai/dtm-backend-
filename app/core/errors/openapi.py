"""Problem responses in the OpenAPI document.

FastAPI documents a response `model` under the route's media type
(application/json). Error responses are application/problem+json, so
`document_problems` post-processes the generated document:

- a response declared with `problem_openapi_response` gets its model's `$ref` moved
  under application/problem+json;
- FastAPI's default 422 (HTTPValidationError) is replaced by `ValidationProblem`;
- every operation documents the 500 `internal_error` problem;
- `ProblemDetails` and `ValidationProblem` are always shared components.
"""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.core.errors.handlers import INTERNAL_ERROR_DETAIL, VALIDATION_DETAIL
from app.core.errors.problem import PROBLEM_MEDIA_TYPE, ProblemDetails, ValidationProblem

_JSON = "application/json"
_REF_TEMPLATE = "#/components/schemas/{model}"
_FASTAPI_VALIDATION_COMPONENTS = ("HTTPValidationError", "ValidationError")
_EXAMPLE_REQUEST_ID = "0190f3c2-7b1e-7c4a-9d2f-3a1b2c3d4e5f"

_INTERNAL_ERROR_RESPONSE = {
    "description": "Unexpected server error; the cause is only logged.",
    "content": {
        PROBLEM_MEDIA_TYPE: {
            "schema": {"$ref": _REF_TEMPLATE.format(model=ProblemDetails.__name__)},
            "example": {
                "type": "/problems/internal-error",
                "title": "Internal Server Error",
                "status": 500,
                "detail": INTERNAL_ERROR_DETAIL,
                "code": "internal_error",
                "request_id": _EXAMPLE_REQUEST_ID,
            },
        },
    },
}
_VALIDATION_RESPONSE = {
    "description": "The request is invalid; the rejected values are not echoed.",
    "content": {
        PROBLEM_MEDIA_TYPE: {
            "schema": {"$ref": _REF_TEMPLATE.format(model=ValidationProblem.__name__)},
            "example": {
                "type": "/problems/validation-error",
                "title": "Validation failed",
                "status": 422,
                "detail": VALIDATION_DETAIL,
                "code": "validation_error",
                "request_id": _EXAMPLE_REQUEST_ID,
                "errors": [
                    {
                        "loc": ["query", "limit"],
                        "message": "Input should be a valid integer",
                        "type": "int_parsing",
                    }
                ],
            },
        },
    },
}


def problem_openapi_response(
    model: type[ProblemDetails],
    description: str,
    example: Mapping[str, object],
) -> dict[str, Any]:
    """Describe a problem response for an operation's `responses` mapping.

    The model may contain nested models; FastAPI registers them as components and
    `document_problems` moves the reference under application/problem+json.
    """
    return {
        "model": model,
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"example": dict(example)}},
    }


def _add_component(schemas: dict[str, Any], model: type[BaseModel]) -> None:
    generated = model.model_json_schema(ref_template=_REF_TEMPLATE)
    for name, nested in generated.pop("$defs", {}).items():
        schemas.setdefault(name, nested)
    schemas.setdefault(model.__name__, generated)


def _move_problem_schema(response: dict[str, Any]) -> None:
    content = response.get("content", {})
    problem = content.get(PROBLEM_MEDIA_TYPE)
    if problem is None or "schema" in problem or _JSON not in content:
        return
    problem["schema"] = content.pop(_JSON)["schema"]


def _is_fastapi_validation_response(response: dict[str, Any]) -> bool:
    schema = response.get("content", {}).get(_JSON, {}).get("schema", {})
    return bool(schema.get("$ref", "").endswith("/HTTPValidationError"))


def document_problems(openapi: dict[str, Any]) -> None:
    """Rewrite a generated OpenAPI document in place, as described in the module doc."""
    schemas: dict[str, Any] = openapi.setdefault("components", {}).setdefault("schemas", {})
    _add_component(schemas, ProblemDetails)
    _add_component(schemas, ValidationProblem)
    for path_item in openapi.get("paths", {}).values():
        for operation in path_item.values():
            responses: dict[str, Any] = operation.setdefault("responses", {})
            for response in responses.values():
                _move_problem_schema(response)
            if _is_fastapi_validation_response(responses.get("422", {})):
                responses["422"] = json.loads(json.dumps(_VALIDATION_RESPONSE))
            responses.setdefault("500", json.loads(json.dumps(_INTERNAL_ERROR_RESPONSE)))
    if "/HTTPValidationError" not in json.dumps(openapi.get("paths", {})):
        for name in _FASTAPI_VALIDATION_COMPONENTS:
            schemas.pop(name, None)
