"""Request correlation id: header name and resolution rule.

`RequestContextMiddleware` (app.core.middleware) resolves the id once per request,
stores it on `request.state.request_id` and sets the response header. Error
handlers read it with `current_request_id`, which falls back to the same rule
only when no middleware ran (a handler called directly).
"""

import re

import uuid_utils
from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def resolve_request_id(request: Request) -> str:
    """Reuse a well-formed incoming X-Request-ID, otherwise mint a UUIDv7."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    if _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return str(uuid_utils.uuid7())


def current_request_id(request: Request) -> str:
    """Return the id already resolved for this request, or resolve it now."""
    stored = getattr(request.state, "request_id", None)
    if isinstance(stored, str) and stored:
        return stored
    return resolve_request_id(request)
