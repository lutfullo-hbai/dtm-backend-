"""Shared test setup: real PostgreSQL and Redis containers, markers, the query counter.

The database is never mocked. Integration tests run against containers started once
per session (testcontainers); the schema comes from `alembic upgrade head`, exactly
as in production, never from `create_all()`.

Container images are defined here and nowhere else in the test suite. CI and compose
pin the same tags; override with TEST_POSTGRES_IMAGE / TEST_REDIS_IMAGE.

Exposure: container ports are published on 127.0.0.1 only (a local Docker daemon is
assumed), and testcontainers' Ryuk reaper is disabled by default, because it mounts
the Docker socket and publishes its own port on every interface. Without Ryuk,
cleanup is ours: a container that fails or is interrupted while starting is removed
in `_start`, a started one in its fixture's `finally`, and SIGTERM (CI cancellation,
an IDE's stop button) is turned into KeyboardInterrupt so that session teardown still
runs. Only SIGKILL (or a second interrupt during teardown) can leave containers behind;
they are labelled `org.testcontainers=true` and `make test-clean` removes them. An
ephemeral CI runner may opt back in with TESTCONTAINERS_RYUK_DISABLED=false.
"""

import os
import signal
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from types import FrameType
from typing import NoReturn, cast

import pytest
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from pydantic import SecretStr
from redis import Redis
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import DockerContainer

from app.core.config import Settings
from tests.support.query_counter import QueryCounter, assert_max_queries

POSTGRES_IMAGE = os.environ.get("TEST_POSTGRES_IMAGE", "postgres:18")
REDIS_IMAGE = os.environ.get("TEST_REDIS_IMAGE", "redis:8")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
POSTGRES_USER = "dtm"
POSTGRES_PASSWORD = "dtm"  # throwaway container credentials, never a real secret
POSTGRES_DB = "dtm_test"
# Tests own a Redis database index of their own and flush it before each test.
REDIS_TEST_DB = 1

LOOPBACK = "127.0.0.1"

if "TESTCONTAINERS_RYUK_DISABLED" not in os.environ:
    testcontainers_config.ryuk_disabled = True

# A test that (directly or through other fixtures) needs one of these needs Docker.
_CONTAINER_FIXTURES = frozenset({"postgres_container", "redis_container"})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark each test `integration` (needs Docker) or `unit` (needs nothing)."""
    for item in items:
        fixtures = set(getattr(item, "fixturenames", ()))
        is_integration = not _CONTAINER_FIXTURES.isdisjoint(fixtures)
        item.add_marker(pytest.mark.integration if is_integration else pytest.mark.unit)


def _raise_keyboard_interrupt(signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt(signal.Signals(signum).name)


_PREVIOUS_SIGTERM_HANDLER = pytest.StashKey[signal.Handlers | Callable[..., object] | int | None]()


def pytest_configure(config: pytest.Config) -> None:
    """Handle SIGTERM like Ctrl-C, so fixture teardown stops the containers.

    The default SIGTERM disposition kills the process without running `finally`
    blocks; CI cancellation and IDE "stop" buttons send SIGTERM.
    """
    previous = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    config.stash[_PREVIOUS_SIGTERM_HANDLER] = previous


def pytest_unconfigure(config: pytest.Config) -> None:
    previous = config.stash.get(_PREVIOUS_SIGTERM_HANDLER, None)
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def _docker_unavailable(error: DockerException) -> NoReturn:
    pytest.fail(
        f"Integration tests need a running Docker daemon ({type(error).__name__}: {error}). "
        "Start Docker, or run `make test-unit` for the tests that need none.",
        pytrace=False,
    )


def _start[ContainerT: DockerContainer](create: Callable[[], ContainerT]) -> ContainerT:
    """Create and start a container; without Docker, fail loudly instead of skipping.

    Creating the container object already contacts the Docker daemon. If starting
    fails or is interrupted (Ctrl-C, SIGTERM) after Docker created the container, the
    container is removed before the exception propagates: no fixture owns it yet.
    """
    try:
        container = create()
    except DockerException as error:
        _docker_unavailable(error)
    try:
        return container.start()
    except BaseException as error:
        container.stop()
        if isinstance(error, DockerException):
            _docker_unavailable(error)
        raise


def _on_loopback[ContainerT: DockerContainer](container: ContainerT, port: int) -> ContainerT:
    """Publish `port` on a random free port of 127.0.0.1 instead of every interface.

    docker-py takes a `(host_ip, host_port)` binding; testcontainers annotates the
    value as a host port only, hence the cast. `None` lets Docker pick the port.
    """
    container.ports[str(port)] = cast("int", (LOOPBACK, None))
    return container


def _postgres() -> PostgresContainer:
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
        driver="asyncpg",
    )
    return _on_loopback(container, container.port)


def _redis() -> RedisContainer:
    container = RedisContainer(REDIS_IMAGE)
    return _on_loopback(container, container.port)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    container = _start(_postgres)
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    container = _start(_redis)
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """URL of the session database, migrated to head once per session."""
    url = postgres_container.get_connection_url(host=LOOPBACK)
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return url


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    port = redis_container.get_exposed_port(redis_container.port)
    return f"redis://{LOOPBACK}:{port}/{REDIS_TEST_DB}"


@pytest.fixture(autouse=True)
def _clean_redis(request: pytest.FixtureRequest) -> None:
    """Every test that uses Redis starts with an empty Redis database (the tests' own).

    Keyed on the container fixture, so any test reaching Redis, through whatever
    fixture chain, is isolated, and tests that never touch Redis pay nothing.
    """
    if "redis_container" not in request.fixturenames:
        return
    client = Redis.from_url(request.getfixturevalue("redis_url"))
    try:
        client.flushdb()
    finally:
        client.close()


@pytest.fixture(scope="session")
def test_settings(database_url: str, redis_url: str) -> Settings:
    """Application settings pointing at the test containers."""
    return Settings(
        app_env="test",
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
    )


@pytest.fixture(name="assert_max_queries")
def assert_max_queries_fixture() -> Callable[[int], AbstractContextManager[QueryCounter]]:
    """`with assert_max_queries(n): ...` fails if the block sends more than n statements."""
    return assert_max_queries
