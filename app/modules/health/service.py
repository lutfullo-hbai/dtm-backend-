"""Readiness checks against the backing services.

The service owns the policy (timeouts, single-flight, abandonment); the probes
themselves live in the repository and are injected as plain callables.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import AppError
from app.modules.health.schemas import CheckStatus

logger = logging.getLogger(__name__)

Probe = Callable[[], Awaitable[None]]
NOT_READY_DETAIL = "One or more dependencies are unavailable."


class NotReadyError(AppError):
    """At least one backing service is unavailable."""

    status = 503
    code = "not_ready"
    title = "Service not ready"

    def __init__(self, checks: dict[str, CheckStatus]) -> None:
        super().__init__(NOT_READY_DETAIL, extensions={"checks": dict(checks)})


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Outcome of one readiness probe."""

    database: CheckStatus
    redis: CheckStatus

    @property
    def is_ready(self) -> bool:
        """True when every dependency answered in time."""
        return self.database == "ok" and self.redis == "ok"


@dataclass(slots=True)
class _Flight:
    """One in-flight check shared by every caller that arrives while it runs."""

    task: asyncio.Task[ReadinessReport]
    waiters: int = 0


class ReadinessService:
    """Probes PostgreSQL and Redis concurrently, each bounded by a timeout.

    Single-flight: concurrent callers share one in-flight check, so a burst of
    requests costs one database and one Redis probe at a time per process. A
    result is never reused once its check has finished.
    """

    def __init__(
        self,
        database_probe: Probe,
        redis_probe: Probe,
        timeout_seconds: float,
        *,
        drain_timeout_seconds: float = 3.0,
    ) -> None:
        self._database_probe = database_probe
        self._redis_probe = redis_probe
        self._timeout_seconds = timeout_seconds
        self._drain_timeout_seconds = drain_timeout_seconds
        self._flight: _Flight | None = None
        # Timed-out probes still clean up after cancellation; keep references so
        # they are not garbage-collected mid-cleanup, and so aclose() can await them.
        self._abandoned: set[asyncio.Task[CheckStatus]] = set()

    async def ensure_ready(self) -> ReadinessReport:
        """Return the report when every dependency is ready.

        Raises:
            NotReadyError: if any dependency is unavailable; carries `checks`.
        """
        report = await self.check()
        if not report.is_ready:
            raise NotReadyError({"database": report.database, "redis": report.redis})
        return report

    async def check(self) -> ReadinessReport:
        """Join the in-flight check, or start one. Never raises for a failing probe.

        A cancelled caller detaches without disturbing the others; when the last
        waiting caller is cancelled the shared check and its probes are cancelled.
        A check whose cancellation has been requested is never joined: it can only
        end cancelled, so a caller arriving in that window starts a new one.
        """
        flight = self._flight
        if flight is None or flight.task.done() or flight.task.cancelling():
            flight = _Flight(asyncio.create_task(self._run_checks()))
            self._flight = flight
            flight.task.add_done_callback(lambda _task: self._forget(flight))
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                flight.task.cancel()

    async def aclose(self) -> None:
        """Cancel any in-flight check and wait, bounded, for probes to clean up.

        Must run before the engine and Redis client are closed, so no probe is
        still using them.
        """
        pending: set[asyncio.Task[object]] = set(self._abandoned)
        if self._flight is not None and not self._flight.task.done():
            self._flight.task.cancel()
            pending.add(self._flight.task)
        if not pending:
            return
        try:
            async with asyncio.timeout(self._drain_timeout_seconds):
                await asyncio.gather(*pending, return_exceptions=True)
        except TimeoutError:
            logger.warning("readiness probes still running at shutdown: %d", len(pending))
        # Done-callbacks may not have run yet; drop finished tasks deterministically.
        self._abandoned = {task for task in self._abandoned if not task.done()}

    def _forget(self, flight: _Flight) -> None:
        if self._flight is flight:
            self._flight = None

    async def _run_checks(self) -> ReadinessReport:
        """Run both probes concurrently and wait at most the timeout.

        The deadline is enforced with `asyncio.wait` rather than awaiting the
        cancelled probe, because cancellation cleanup can itself block (SQLAlchemy
        closes an asyncpg connection gracefully with a 2 s timeout). The response
        time therefore stays at about READINESS_TIMEOUT_SECONDS.
        """
        database = asyncio.create_task(self._probe("database", self._database_probe))
        redis = asyncio.create_task(self._probe("redis", self._redis_probe))
        try:
            await asyncio.wait((database, redis), timeout=self._timeout_seconds)
        except asyncio.CancelledError:  # every caller gave up, or shutdown
            self._abandon(database)
            self._abandon(redis)
            raise
        return ReadinessReport(
            database=self._result("database", database),
            redis=self._result("redis", redis),
        )

    def _result(self, name: str, task: asyncio.Task[CheckStatus]) -> CheckStatus:
        if task.done():
            return task.result()
        logger.warning("readiness check timed out: %s after %ss", name, self._timeout_seconds)
        self._abandon(task)
        return "unavailable"

    def _abandon(self, task: asyncio.Task[CheckStatus]) -> None:
        if task.done():
            return
        task.cancel()
        self._abandoned.add(task)
        task.add_done_callback(self._abandoned.discard)

    @staticmethod
    async def _probe(name: str, ping: Probe) -> CheckStatus:
        try:
            await ping()
        except Exception:
            # Any failure (refused, auth, protocol) means "not ready"; the cause is
            # logged here and deliberately kept out of the HTTP response.
            logger.warning("readiness check failed: %s", name, exc_info=True)
            return "unavailable"
        return "ok"
