"""The CI workflow keeps the guarantees the branch rulesets and the plan rely on."""

import re
from pathlib import Path

from tests.conftest import DEFAULT_POSTGRES_IMAGE

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_DOCKERFILE = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

# The rulesets require these checks by name; renaming a job silently drops a check.
_REQUIRED_CHECKS = {"lint", "typecheck", "test", "migrations", "build"}


def test_jobs_are_exactly_the_required_checks() -> None:
    jobs = _WORKFLOW.split("\njobs:\n", 1)[1]
    ids = set(re.findall(r"^  ([a-z-]+):$", jobs, re.MULTILINE))
    names = set(re.findall(r"^    name: (\S+)$", jobs, re.MULTILINE))

    assert ids == _REQUIRED_CHECKS
    assert names == _REQUIRED_CHECKS


def test_every_job_runs_on_a_github_hosted_runner() -> None:
    runners = re.findall(r"^\s+runs-on:\s*(.+)$", _WORKFLOW, re.MULTILINE)

    assert runners == ["ubuntu-latest"] * len(_REQUIRED_CHECKS)


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", _WORKFLOW)

    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses), uses


def test_migrations_run_against_the_postgres_image_the_tests_use() -> None:
    images = re.findall(r"^\s+image:\s*(\S+)\s*$", _WORKFLOW, re.MULTILINE)

    assert images == [DEFAULT_POSTGRES_IMAGE]


def test_uv_version_matches_the_dockerfile() -> None:
    (ci_uv,) = re.findall(r'UV_VERSION:\s*"([\d.]+)"', _WORKFLOW)
    (image_uv,) = re.findall(r"ghcr\.io/astral-sh/uv:([\d.]+)", _DOCKERFILE)

    assert ci_uv == image_uv


def test_python_version_matches_the_dockerfile() -> None:
    """CI's uv installs the interpreter named in .python-version; the image runs this one."""
    pinned = (_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    image_pythons = set(re.findall(r"^FROM python:([\d.]+)-", _DOCKERFILE, re.MULTILINE))

    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), pinned
    assert image_pythons == {pinned}


def test_never_runs_on_pull_request_target() -> None:
    """pull_request_target runs a fork's PR with the base repository's secrets."""
    assert "pull_request_target" not in _WORKFLOW


def test_top_level_permissions_are_read_only() -> None:
    top_level = _WORKFLOW.split("\njobs:\n", 1)[0]

    assert re.search(r"^permissions:\n  contents: read\n(?!  )", top_level, re.MULTILINE)
    # No job widens them.
    assert _WORKFLOW.count("permissions:") == 1


def test_every_checkout_drops_its_credentials() -> None:
    """A step after checkout must not find the token persisted in .git/config."""
    steps = re.split(r"\n\s+- ", _WORKFLOW)
    checkouts = [step for step in steps if re.match(r"uses:\s*actions/checkout@", step)]

    assert len(checkouts) == len(_REQUIRED_CHECKS)
    assert all(re.search(r"^\s+persist-credentials: false$", s, re.MULTILINE) for s in checkouts)


def test_every_make_target_the_workflow_runs_exists() -> None:
    called = set(re.findall(r"\bmake ([a-z][a-z-]*)", _WORKFLOW))
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    defined = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.MULTILINE))

    assert called
    assert called <= defined, called - defined
