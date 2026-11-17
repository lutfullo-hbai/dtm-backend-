"""A failing migration run from the CLI exits non-zero without printing database values.

The real migrations/env.py is copied, with the real history, next to a throwaway
revision (written to tmp_path, never committed) that violates a unique constraint, and
`alembic upgrade head` runs as a subprocess, as the one-off migrate process does.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from testcontainers.community.postgres import PostgresContainer

from tests.conftest import ALEMBIC_INI, LOOPBACK, POSTGRES_DB, POSTGRES_USER

_ROOT = ALEMBIC_INI.parent
_PERSONAL = "pii-user@migration-secret.example"
_FAILING_REVISION = '''"""Throwaway: inserts one value twice into a unique column."""

import os

import sqlalchemy as sa
from alembic import op

revision = "f0000000fail"
down_revision = {head!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE migration_failure_probe (email text UNIQUE)")
    insert = sa.text("INSERT INTO migration_failure_probe (email) VALUES (:email)")
    value = os.environ["MIGRATION_PROBE_VALUE"]  # not in this source: frames show code
    op.get_bind().execute(insert, {{"email": value}})
    op.get_bind().execute(insert, {{"email": value}})


def downgrade() -> None:
    op.execute("DROP TABLE migration_failure_probe")
'''


def _psql(container: PostgresContainer, sql: str) -> None:
    result = container.exec(
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", POSTGRES_USER, "-d", POSTGRES_DB, "-c", sql]
    )
    assert result.exit_code == 0, result.output.decode()


@pytest.fixture
def fresh_database_url(postgres_container: PostgresContainer) -> Iterator[str]:
    name = f"migration_failure_{uuid4().hex}"
    _psql(postgres_container, f'CREATE DATABASE "{name}"')
    url = postgres_container.get_connection_url(host=LOOPBACK).rsplit("/", 1)[0] + f"/{name}"
    yield url
    _psql(postgres_container, f'DROP DATABASE "{name}" WITH (FORCE)')


def _script_location_with_failing_revision(tmp_path: Path) -> Path:
    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    location = tmp_path / "migrations"
    shutil.copytree(_ROOT / "migrations", location, ignore=shutil.ignore_patterns("__pycache__"))
    revision = _FAILING_REVISION.format(head=head)
    (location / "versions" / "9999_12_31_2359_failing_probe.py").write_text(revision)
    return location


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_failed_migration_exits_non_zero_without_values(
    tmp_path: Path, fresh_database_url: str, log_format: str
) -> None:
    location = _script_location_with_failing_revision(tmp_path)
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        ALEMBIC_INI.read_text().replace("%(here)s/migrations", str(location)),
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_ROOT),
        "APP_ENV": "test",
        "DATABASE_URL": fresh_database_url,
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "LOG_FORMAT": log_format,
        "MIGRATION_PROBE_VALUE": _PERSONAL,
    }

    result = subprocess.run(  # noqa: S603  # fixed argv: this interpreter, alembic, a tmp ini
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "migration_failed" in result.stdout
    assert "IntegrityError" in result.stdout  # the type is kept, the message is not
    assert "migration-secret" not in output
