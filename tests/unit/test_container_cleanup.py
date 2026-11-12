"""Test containers are removed when starting them fails or is interrupted."""

import signal
from typing import Self

import pytest
from docker.errors import DockerException
from testcontainers.core.container import DockerContainer

from tests.conftest import _raise_keyboard_interrupt, _start


class _FakeContainer(DockerContainer):
    """Fails in `start()` with a given exception; records whether it was stopped."""

    def __init__(self, error: BaseException) -> None:  # no Docker client is created
        self.error = error
        self.stopped = False

    def start(self) -> Self:
        raise self.error

    def stop(self, *_args: object, **_kwargs: object) -> None:
        self.stopped = True


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("wait timed out")])
def test_a_container_interrupted_while_starting_is_removed(error: BaseException) -> None:
    container = _FakeContainer(error)

    with pytest.raises(type(error)):
        _start(lambda: container)

    assert container.stopped


def test_docker_failing_mid_start_removes_the_container_and_fails_the_test() -> None:
    container = _FakeContainer(DockerException("daemon went away"))

    with pytest.raises(pytest.fail.Exception, match="need a running Docker daemon"):
        _start(lambda: container)

    assert container.stopped


def test_no_docker_at_all_fails_the_test_instead_of_skipping() -> None:
    def create() -> DockerContainer:
        raise DockerException("no socket")

    with pytest.raises(pytest.fail.Exception, match="need a running Docker daemon"):
        _start(create)


def test_sigterm_is_handled_like_ctrl_c_during_the_run() -> None:
    assert signal.getsignal(signal.SIGTERM) is _raise_keyboard_interrupt

    with pytest.raises(KeyboardInterrupt, match="SIGTERM"):
        _raise_keyboard_interrupt(signal.SIGTERM, None)
