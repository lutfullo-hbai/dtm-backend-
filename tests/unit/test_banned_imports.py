"""Lint rule: models cannot reach SQLAlchemy's relationship helpers directly.

Runs the project's real ruff configuration over a snippet, so removing the
banned-api entries from pyproject.toml makes this test fail.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _ruff_banned_api(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "TID251",
            "--output-format",
            "concise",
            "--stdin-filename",
            "app/modules/example/models.py",
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy.orm import relationship\n",
        "from sqlalchemy.orm import backref\n",
        "from sqlalchemy import orm\n\nrel = orm.relationship\n",
    ],
)
def test_direct_relationship_helpers_fail_lint(source: str) -> None:
    result = _ruff_banned_api(source)

    assert result.returncode == 1
    assert "TID251" in result.stdout


def test_project_wrapper_passes_lint() -> None:
    result = _ruff_banned_api("from app.core.db import relationship\n")

    assert result.returncode == 0, result.stdout
