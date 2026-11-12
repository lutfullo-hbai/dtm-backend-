"""compose.yaml and the test containers run the same PostgreSQL and Redis images."""

import re
from pathlib import Path

from tests.conftest import DEFAULT_POSTGRES_IMAGE, DEFAULT_REDIS_IMAGE

_COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"
_IMAGE = re.compile(r"^\s+image:\s*(\S+)\s*$", re.MULTILINE)


def _images() -> set[str]:
    return set(_IMAGE.findall(_COMPOSE.read_text(encoding="utf-8")))


def test_compose_runs_the_images_the_tests_run() -> None:
    images = _images()

    assert DEFAULT_POSTGRES_IMAGE in images
    assert DEFAULT_REDIS_IMAGE in images


def test_every_third_party_image_is_pinned_to_a_patch_version() -> None:
    third_party = {image for image in _images() if not image.startswith("dtm-backend:")}

    assert third_party
    assert all(re.fullmatch(r"[a-z0-9./-]+:\d+\.\d+(\.\d+)?", image) for image in third_party)


def _service(name: str) -> str:
    """The text of one service block (the lines indented below `  name:`)."""
    match = re.search(
        rf"^  {name}:\n((?:(?:    .*)?\n)+)", _COMPOSE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, name
    return match.group(1)


def test_the_app_image_is_built_once_by_api_and_never_pulled_by_migrate() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    api, migrate = _service("api"), _service("migrate")

    assert len(re.findall(r"^\s+build:", text, re.MULTILINE)) == 1
    assert re.search(r"^    build:$", api, re.MULTILINE)
    assert re.search(r"^    pull_policy: never$", migrate, re.MULTILINE)
    assert "image: dtm-backend:local" in text
    assert re.search(r"^  migrate:\n    <<: \*app$", text, re.MULTILINE)
    assert re.search(r"^  api:\n    <<: \*app$", text, re.MULTILINE)
