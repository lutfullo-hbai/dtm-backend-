"""The CI workflow keeps the guarantees the branch rulesets and the plan rely on.

Checked on the parsed YAML, so a required check cannot be neutralised while its name
stays green: no `continue-on-error`, no job-level `if:` on a required job, no
`|| true` / `exit 0` style escape in a run step, and every required job still runs
its Makefile target as a plain step.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import DEFAULT_POSTGRES_IMAGE

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_TEXT = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_DOCKERFILE = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

# The rulesets require these checks by name; renaming a job silently drops a check.
# Each one must run these Makefile targets, each as a step of its own.
_REQUIRED_TARGETS = {
    "lint": ["ci-lint"],
    "typecheck": ["typecheck"],
    "test": ["test"],
    "migrations": ["test-migrations", "migrations-roundtrip", "openapi-check"],
    "build": ["image"],
}
_REQUIRED_CHECKS = set(_REQUIRED_TARGETS)
# A step's command must be exactly `make <target>`, optionally with VAR="value" overrides.
_MAKE_STEP = r"make {target}(?: [A-Z_]+=\"[^\"\n]*\")*"
# Shell idioms that turn a failing command into a passing step.
_NEUTRALISERS = re.compile(
    r"\|\|\s*(?:true|:)(?:\s|;|$)|;\s*true\b|\bexit\s+0\b|\bset\s+\+e\b", re.MULTILINE
)


def _load(text: str) -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(text)
    return workflow


_WORKFLOW = _load(_WORKFLOW_TEXT)


def _jobs(workflow: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = workflow["jobs"]
    return jobs


def _steps(workflow: dict[Any, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_id, job in _jobs(workflow).items():
        for step in job.get("steps", []):
            yield job_id, step


def _keys(node: object) -> Iterator[str]:
    """Every mapping key anywhere in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _neutralised_checks(workflow: dict[Any, Any]) -> list[str]:
    """Why a required check could pass without running its target; empty when none."""
    problems = [f"{key} in the workflow" for key in _keys(workflow) if key == "continue-on-error"]
    jobs = _jobs(workflow)
    for job_id, targets in _REQUIRED_TARGETS.items():
        job = jobs.get(job_id, {})
        if "if" in job:
            problems.append(f"{job_id}: job-level if: {job['if']!r}")
        steps = job.get("steps", [])
        for target in targets:
            plain = [
                s
                for s in steps
                if "if" not in s
                and re.fullmatch(_MAKE_STEP.format(target=target), str(s.get("run", "")).strip())
            ]
            if len(plain) != 1:
                problems.append(f"{job_id}: no unconditional `make {target}` step")
    problems.extend(
        f"{job_id}: run step escapes failure: {step['run']!r}"
        for job_id, step in _steps(workflow)
        if _NEUTRALISERS.search(str(step.get("run", "")))
    )
    return problems


def test_jobs_are_exactly_the_required_checks() -> None:
    jobs = _jobs(_WORKFLOW)

    assert set(jobs) == _REQUIRED_CHECKS
    assert {job["name"] for job in jobs.values()} == _REQUIRED_CHECKS


def test_no_required_check_is_neutralised() -> None:
    assert _neutralised_checks(_WORKFLOW) == []


_LINT_JOB = "  lint:\n    name: lint\n"
_TEST_STEP = "        run: make test\n"


_MUTATIONS = {
    "failure swallowed": (_TEST_STEP, "        run: make test || true\n"),
    "job skipped": (_LINT_JOB, _LINT_JOB + "    if: false\n"),
    "job may fail": (_LINT_JOB, _LINT_JOB + "    continue-on-error: true\n"),
    "step exits early": (_TEST_STEP, "        run: |\n          exit 0\n          make test\n"),
    "target removed": (_TEST_STEP, "        run: echo skipped\n"),
    "step made conditional": (_TEST_STEP, _TEST_STEP + "        if: false\n"),
}


@pytest.mark.parametrize("description", sorted(_MUTATIONS))
def test_a_neutralised_required_check_is_detected(description: str) -> None:
    old, new = _MUTATIONS[description]
    assert _WORKFLOW_TEXT.count(old) == 1
    mutated = _load(_WORKFLOW_TEXT.replace(old, new))

    assert _neutralised_checks(mutated) != []


def test_every_job_runs_on_a_github_hosted_runner() -> None:
    assert [job["runs-on"] for job in _jobs(_WORKFLOW).values()] == ["ubuntu-latest"] * len(
        _REQUIRED_CHECKS
    )


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = [step["uses"] for _job, step in _steps(_WORKFLOW) if "uses" in step]

    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses), uses


def test_migrations_run_against_the_postgres_image_the_tests_use() -> None:
    images = [
        service["image"]
        for job in _jobs(_WORKFLOW).values()
        for service in job.get("services", {}).values()
    ]

    assert images == [DEFAULT_POSTGRES_IMAGE]


def test_uv_version_matches_the_dockerfile() -> None:
    (image_uv,) = re.findall(r"ghcr\.io/astral-sh/uv:([\d.]+)", _DOCKERFILE)

    assert _WORKFLOW["env"]["UV_VERSION"] == image_uv


def test_python_version_matches_the_dockerfile() -> None:
    """CI's uv installs the interpreter named in .python-version; the image runs this one."""
    pinned = (_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    image_pythons = set(re.findall(r"^FROM python:([\d.]+)-", _DOCKERFILE, re.MULTILINE))

    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), pinned
    assert image_pythons == {pinned}


def test_never_runs_on_pull_request_target() -> None:
    """pull_request_target runs a fork's PR with the base repository's secrets."""
    triggers = _WORKFLOW[True]  # YAML 1.1 reads the `on` key as the boolean True

    assert set(triggers) == {"pull_request", "push"}
    assert "pull_request_target" not in _WORKFLOW_TEXT


def test_permissions_are_read_only_and_never_widened() -> None:
    assert _WORKFLOW["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in _jobs(_WORKFLOW).values())


def test_every_checkout_drops_its_credentials() -> None:
    """A step after checkout must not find the token persisted in .git/config."""
    checkouts = [
        step
        for _job, step in _steps(_WORKFLOW)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert len(checkouts) == len(_REQUIRED_CHECKS)
    assert all(step["with"]["persist-credentials"] is False for step in checkouts)


def test_every_make_target_the_workflow_runs_exists() -> None:
    called = set(re.findall(r"\bmake ([a-z][a-z-]*)", _WORKFLOW_TEXT))
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    defined = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.MULTILINE))

    assert called
    assert called <= defined, called - defined
