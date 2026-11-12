"""Migration history checks (pytest-alembic), each on a fresh, empty database.

- single head: two heads would make `upgrade head` ambiguous (plan, Alembic rule 8);
- upgrade: base -> head applies every revision;
- model definitions match DDL: autogenerate against the migrated schema finds no diff;
- up/down consistency: every revision upgrades and downgrades (Alembic rule 4).
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic.config import Config
from pytest_alembic.tests import (
    test_model_definitions_match_ddl,
    test_single_head_revision,
    test_up_down_consistency,
    test_upgrade,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from tests.conftest import ALEMBIC_INI, LOOPBACK, POSTGRES_DB, POSTGRES_USER

__all__ = [
    "test_model_definitions_match_ddl",
    "test_single_head_revision",
    "test_up_down_consistency",
    "test_upgrade",
]


def _psql(container: PostgresContainer, sql: str) -> None:
    result = container.exec(
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", POSTGRES_USER, "-d", POSTGRES_DB, "-c", sql]
    )
    assert result.exit_code == 0, result.output.decode()


@pytest.fixture
def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def alembic_engine(postgres_container: PostgresContainer) -> Iterator[AsyncEngine]:
    """A brand-new database per test, so history tests never see the session schema."""
    name = f"migrations_{uuid4().hex}"
    _psql(postgres_container, f'CREATE DATABASE "{name}"')
    url = postgres_container.get_connection_url(host=LOOPBACK).rsplit("/", 1)[0] + f"/{name}"
    # NullPool: pytest-alembic runs each step in its own event loop (asyncio.run), and
    # an asyncpg connection must not outlive the loop that opened it.
    yield create_async_engine(url, poolclass=NullPool)
    _psql(postgres_container, f'DROP DATABASE "{name}" WITH (FORCE)')
