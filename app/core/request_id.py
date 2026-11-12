"""Request correlation id: header name and resolution rule.

From M0-08 a middleware resolves the id once per request, stores it on
`request.state.request_id` and sets the response header; until then error
handlers resolve it themselves with the same rule.
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
