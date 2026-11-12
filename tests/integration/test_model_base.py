"""The model base and mixins against real PostgreSQL, through an AsyncSession.

The test-only tables are created inside the test's outer transaction, so the
rollback after the test drops them again (PostgreSQL DDL is transactional); they
never reach Base.metadata, Alembic, or another test.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import ForeignKey, MetaData, String, select, text, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, selectinload
from sqlalchemy.pool import NullPool

from app.core.db import AsyncSession, Base, TimestampMixin, UUIDPrimaryKeyMixin, relationship
from tests.support.query_counter import QueryCounter


class _ItBase(Base):
    __abstract__ = True
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)


class _Parent(UUIDPrimaryKeyMixin, TimestampMixin, _ItBase):
    __tablename__ = "it_parents"

    name: Mapped[str] = mapped_column(String(32))
    children: Mapped[list["_Child"]] = relationship(back_populates="parent")


class _Child(UUIDPrimaryKeyMixin, _ItBase):
    __tablename__ = "it_children"

    it_parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("it_parents.id"))
    parent: Mapped[_Parent] = relationship(back_populates="children")


@pytest.fixture
async def session(db_connection: AsyncConnection, db_session: AsyncSession) -> AsyncSession:
    await db_connection.run_sync(_ItBase.metadata.create_all)
    return db_session


def _data_statements(counter: QueryCounter) -> list[str]:
    return [sql for sql in counter.statements if "SAVEPOINT" not in sql]


async def test_insert_generates_uuid7_and_returns_timestamps(session: AsyncSession) -> None:
    parent = _Parent(name="first")
    session.add(parent)

    with QueryCounter() as counter:
        await session.flush()

    (insert,) = _data_statements(counter)
    assert insert.startswith("INSERT INTO it_parents")
    assert "RETURNING" in insert
    assert parent.id.version == 7
    assert isinstance(parent.created_at, datetime)
    assert parent.created_at.utcoffset() is not None
    assert parent.updated_at == parent.created_at


async def test_update_sets_updated_at_and_returns_it(session: AsyncSession) -> None:
    parent = _Parent(name="first")
    session.add(parent)
    await session.flush()
    # now() is the transaction start time, constant inside the test's transaction:
    # backdate the row so the UPDATE's now() is observably different.
    await session.execute(
        update(_Parent)
        .where(_Parent.id == parent.id)
        .values(updated_at=text("now() - interval '1 hour'"))
        .execution_options(synchronize_session=False)
    )
    await session.refresh(parent)
    backdated = parent.updated_at

    parent.name = "renamed"
    with QueryCounter() as counter:
        await session.flush()

    (statement,) = _data_statements(counter)
    assert statement.startswith("UPDATE it_parents SET name=")
    assert "updated_at=now()" in statement
    assert "RETURNING" in statement
    assert parent.updated_at > backdated
    assert parent.updated_at == parent.created_at


async def test_relationships_raise_instead_of_lazy_loading(session: AsyncSession) -> None:
    parent = _Parent(name="first", children=[_Child(), _Child()])
    session.add(parent)
    await session.flush()
    session.expunge_all()

    loaded = (await session.execute(select(_Parent))).scalar_one()
    with pytest.raises(InvalidRequestError, match="lazy='raise'"):
        _ = loaded.children

    session.expunge_all()
    eager = select(_Parent).options(selectinload(_Parent.children))
    loaded = (await session.execute(eager)).scalar_one()
    assert len(loaded.children) == 2


async def test_test_tables_never_leave_the_test_transaction(
    session: AsyncSession, database_url: str
) -> None:
    session.add(_Parent(name="first"))
    await session.flush()

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            visible = await connection.scalar(text("SELECT to_regclass('it_parents')"))
    finally:
        await engine.dispose()

    assert visible is None
