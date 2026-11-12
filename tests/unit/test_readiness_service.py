"""ReadinessService concurrency logic, with fake probes instead of real services."""

import asyncio

import pytest

from app.modules.health.service import NotReadyError, Probe, ReadinessService

_TIMEOUT_SECONDS = 0.2


class _ProbeSpy:
    """A probe that counts calls, can block until released, and records cancellation."""

    def __init__(self, *, block: bool = False, error: Exception | None = None) -> None:
        self.calls = 0
        self.was_cancelled = asyncio.Event()
        self.started = asyncio.Event()
        self._release = asyncio.Event()
        if not block:
            self._release.set()
        self._error = error

    def release(self) -> None:
        self._release.set()

    async def __call__(self) -> None:
        self.calls += 1
        self.started.set()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.was_cancelled.set()
            raise
        if self._error is not None:
            raise self._error


def _service(
    database: Probe, redis: Probe, timeout_seconds: float = _TIMEOUT_SECONDS
) -> ReadinessService:
    return ReadinessService(database, redis, timeout_seconds, drain_timeout_seconds=1.0)


async def test_both_probes_ok_is_ready() -> None:
    service = _service(_ProbeSpy(), _ProbeSpy())

    report = await service.ensure_ready()

    assert (report.database, report.redis) == ("ok", "ok")


async def test_failing_probe_is_unavailable_without_raising() -> None:
    service = _service(_ProbeSpy(error=ConnectionError("refused")), _ProbeSpy())

    report = await service.check()

    assert (report.database, report.redis) == ("unavailable", "ok")


async def test_ensure_ready_raises_not_ready_with_checks() -> None:
    service = _service(_ProbeSpy(), _ProbeSpy(error=OSError("down")))

    with pytest.raises(NotReadyError) as raised:
        await service.ensure_ready()

    assert raised.value.status == 503
    assert raised.value.extensions == {"checks": {"database": "ok", "redis": "unavailable"}}


async def test_timed_out_probe_is_cancelled_and_abandoned_set_drains() -> None:
    hung = _ProbeSpy(block=True)
    service = _service(hung, _ProbeSpy())

    report = await service.check()
    await service.aclose()

    assert (report.database, report.redis) == ("unavailable", "ok")
    assert hung.was_cancelled.is_set()
    assert not service._abandoned


async def test_concurrent_callers_share_one_probe_per_dependency() -> None:
    database, redis = _ProbeSpy(block=True), _ProbeSpy(block=True)
    service = _service(database, redis)

    callers = [asyncio.create_task(service.check()) for _ in range(50)]
    await database.started.wait()
    await redis.started.wait()
    database.release()
    redis.release()
    reports = await asyncio.gather(*callers)

    assert database.calls == 1
    assert redis.calls == 1
    assert {(report.database, report.redis) for report in reports} == {("ok", "ok")}


async def test_finished_result_is_not_reused() -> None:
    database, redis = _ProbeSpy(), _ProbeSpy()
    service = _service(database, redis)

    await service.check()
    await service.check()

    assert (database.calls, redis.calls) == (2, 2)


async def test_cancelling_the_only_caller_cancels_both_probes() -> None:
    database, redis = _ProbeSpy(block=True), _ProbeSpy(block=True)
    # A long readiness timeout, so only the caller's cancellation can stop the probes.
    service = _service(database, redis, timeout_seconds=60)

    caller = asyncio.create_task(service.check())
    await database.started.wait()
    await redis.started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    # Cancellation reaches the probes on its own, before (and without) aclose().
    async with asyncio.timeout(_TIMEOUT_SECONDS):
        await database.was_cancelled.wait()
        await redis.was_cancelled.wait()
    await service.aclose()
    assert not service._abandoned


async def test_cancelling_one_caller_leaves_the_shared_check_running() -> None:
    database, redis = _ProbeSpy(block=True), _ProbeSpy(block=True)
    service = _service(database, redis)

    leaving = asyncio.create_task(service.check())
    staying = asyncio.create_task(service.check())
    await database.started.wait()
    await redis.started.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    database.release()
    redis.release()
    report = await staying

    assert (report.database, report.redis) == ("ok", "ok")
    assert not database.was_cancelled.is_set()
    assert not redis.was_cancelled.is_set()


async def test_caller_arriving_while_the_last_waiter_cancels_starts_a_new_check() -> None:
    database, redis = _ProbeSpy(block=True), _ProbeSpy(block=True)
    service = _service(database, redis, timeout_seconds=60)

    leaving = asyncio.create_task(service.check())
    await database.started.wait()
    await redis.started.wait()
    # `leaving` runs first and cancels the shared check as its last waiter; `arriving`
    # runs in the same loop iteration, before that cancellation has completed.
    leaving.cancel()
    arriving = asyncio.create_task(service.check())
    with pytest.raises(asyncio.CancelledError):
        await leaving
    database.release()
    redis.release()
    report = await arriving

    assert (report.database, report.redis) == ("ok", "ok")
    assert (database.calls, redis.calls) == (2, 2)
    await service.aclose()


async def test_aclose_cancels_an_in_flight_check() -> None:
    database, redis = _ProbeSpy(block=True), _ProbeSpy(block=True)
    service = _service(database, redis)

    caller = asyncio.create_task(service.check())
    await database.started.wait()
    await redis.started.wait()
    await service.aclose()

    with pytest.raises(asyncio.CancelledError):
        await caller
    assert database.was_cancelled.is_set()
    assert redis.was_cancelled.is_set()
