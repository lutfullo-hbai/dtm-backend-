"""Request context: correlation id, response header and one access event per request.

`RequestContextMiddleware` is pure ASGI (not `BaseHTTPMiddleware`, which buffers
and runs the endpoint in a separate task) and wraps the whole application, outside
Starlette's `ServerErrorMiddleware`. It therefore sees every response, including
404/405 from the router, 422 from validation and the 500 rendered for an unhandled
exception, and stamps X-Request-ID on all of them.
"""

import time

import structlog
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.request_id import REQUEST_ID_HEADER, resolve_request_id

logger = get_logger("app.http")

_SERVER_ERROR = 500
# Expected while a dependency is down (/ready answers 503): not a defect, so WARNING.
_SERVICE_UNAVAILABLE = 503
_CLIENT_ERROR = 400
# Liveness probes (the image's HEALTHCHECK polls every 10 s): a successful one is
# logged at DEBUG so it does not flood INFO; a failing one keeps its usual level.
_PROBE_PATHS = frozenset({"/health"})


class RequestContextMiddleware:
    """Resolve the request id, expose it, echo it, and log `http_request` once.

    The id is stored in `scope["state"]` (read as `request.state.request_id`) and
    bound in structlog's contextvars, so every log line written while serving the
    request carries it. Only method, path (never the query string), status and
    duration are logged (plus `aborted: true` when the application failed after the
    response had started and before its last body chunk was sent): no headers, no
    body. A successful liveness probe (`/health`) is logged at DEBUG.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Request(scope))
        scope.setdefault("state", {})["request_id"] = request_id
        status: int | None = None  # stays None if no response was ever started
        completed = False  # the final body chunk (more_body false) has been sent

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, completed
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        aborted = False
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # The traceback is logged here, the only place it is logged. Starlette's
            # ServerErrorMiddleware has already sent the 500 response and re-raised;
            # re-raising further would make the server log it again, outside the
            # request context. Only if no response was started is it left to the server.
            logger.exception("unhandled_exception")
            if status is None:
                raise
            # ServerErrorMiddleware re-raises after sending its complete 500: that
            # response reached the client whole, so it is not aborted. A response that
            # had started but not finished is truncated: the access event says so.
            aborted = not completed
        finally:
            _log_request(scope, status, started, aborted=aborted)
            structlog.contextvars.clear_contextvars()


def _log_request(scope: Scope, status: int | None, started: float, *, aborted: bool) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    if aborted:
        log = logger.error
    elif status == _SERVICE_UNAVAILABLE:
        log = logger.warning
    elif status is None or status >= _SERVER_ERROR:
        log = logger.error
    elif status < _CLIENT_ERROR and scope["path"] in _PROBE_PATHS:
        log = logger.debug
    else:
        log = logger.info
    extra = {"aborted": True} if aborted else {}
    log(
        "http_request",
        method=scope["method"],
        path=scope["path"],
        status=status,
        duration_ms=duration_ms,
        **extra,
    )
