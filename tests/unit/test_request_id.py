"""Request-id resolution in app.core.request_id."""

import pytest
import uuid_utils
from starlette.requests import Request

from app.core.request_id import current_request_id, resolve_request_id


def _request(headers: dict[str, str], state: dict[str, object] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "state": state or {},
    }
    return Request(scope)


def test_well_formed_incoming_id_is_reused() -> None:
    assert resolve_request_id(_request({"X-Request-ID": "a.B_9-z"})) == "a.B_9-z"


@pytest.mark.parametrize("incoming", ["", "bad id!", "x" * 129, "naïve"])
def test_malformed_or_missing_id_is_replaced_by_uuid7(incoming: str) -> None:
    resolved = resolve_request_id(_request({"X-Request-ID": incoming} if incoming else {}))

    assert resolved != incoming
    assert uuid_utils.UUID(resolved).version == 7


def test_current_request_id_prefers_state() -> None:
    request = _request({"X-Request-ID": "header"}, {"request_id": "state"})

    assert current_request_id(request) == "state"
