"""RequestContextMiddleware edge cases that the full application never produces."""

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from tests.support.logs import LogReader


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _discard(_message: Message) -> None:
    return None


def _http_scope() -> Scope:
    return {"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}


async def test_non_http_scopes_pass_through_untouched(logs: LogReader) -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope)

    scope: Scope = {"type": "lifespan"}
    await RequestContextMiddleware(inner)(scope, _receive, _discard)

    assert seen == [{"type": "lifespan"}]
    assert logs.lines() == []


async def test_failure_before_any_response_is_logged_and_re_raised(logs: LogReader) -> None:
    async def inner(_scope: Scope, _receive: Receive, _send: Send) -> None:
        msg = "no response"
        raise RuntimeError(msg)

    scope = _http_scope()
    with pytest.raises(RuntimeError, match="no response"):
        await RequestContextMiddleware(inner)(scope, _receive, _discard)

    request_id = scope["state"]["request_id"]
    (error,) = logs.events("unhandled_exception")
    (access,) = logs.events("http_request")
    assert error["request_id"] == request_id
    assert access["status"] is None
    assert access["level"] == "error"


@pytest.mark.parametrize(
    ("status", "level"),
    [(200, "info"), (404, "info"), (500, "error"), (502, "error"), (503, "warning")],
)
async def test_access_event_level_follows_the_status(
    logs: LogReader, status: int, level: str
) -> None:
    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await RequestContextMiddleware(inner)(_http_scope(), _receive, _discard)

    (access,) = logs.events("http_request")
    assert access["status"] == status
    assert access["level"] == level


@pytest.mark.parametrize(
    ("status", "level"), [(200, "debug"), (304, "debug"), (405, "info"), (500, "error")]
)
async def test_only_a_successful_liveness_probe_drops_to_debug(
    logs: LogReader, status: int, level: str
) -> None:
    configure_logging("DEBUG", "json")

    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = _http_scope()
    scope["path"] = "/health"
    await RequestContextMiddleware(inner)(scope, _receive, _discard)

    (access,) = logs.events("http_request")
    assert access["level"] == level


async def test_failure_after_the_response_started_is_an_aborted_error(logs: LogReader) -> None:
    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        msg = "stream broke"
        raise RuntimeError(msg)

    await RequestContextMiddleware(inner)(_http_scope(), _receive, _discard)

    (access,) = logs.events("http_request")
    assert access["status"] == 200
    assert access["level"] == "error"
    assert access["aborted"] is True
    assert len(logs.events("unhandled_exception")) == 1


async def test_completed_request_has_no_aborted_member(logs: LogReader) -> None:
    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await RequestContextMiddleware(inner)(_http_scope(), _receive, _discard)

    (access,) = logs.events("http_request")
    assert "aborted" not in access


async def test_complete_response_then_raise_is_not_aborted(logs: LogReader) -> None:
    """ServerErrorMiddleware sends its whole 500, then re-raises: an ordinary 500."""

    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b"Internal Server Error"})
        msg = "handler failed"
        raise RuntimeError(msg)

    await RequestContextMiddleware(inner)(_http_scope(), _receive, _discard)

    (access,) = logs.events("http_request")
    assert access["status"] == 500
    assert access["level"] == "error"
    assert "aborted" not in access
    assert len(logs.events("unhandled_exception")) == 1


async def test_failure_mid_stream_is_aborted(logs: LogReader) -> None:
    async def inner(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})
        msg = "stream broke"
        raise RuntimeError(msg)

    await RequestContextMiddleware(inner)(_http_scope(), _receive, _discard)

    (access,) = logs.events("http_request")
    assert access["status"] == 200
    assert access["level"] == "error"
    assert access["aborted"] is True
