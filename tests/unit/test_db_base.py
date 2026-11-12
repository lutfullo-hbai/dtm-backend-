"""Model base, mixins and the lazy="raise" relationship default, checked without a database."""

import uuid
from datetime import datetime
from typing import Any, cast

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_mock_engine,
)
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Mapped, make_transient_to_detached, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.schema import CallableColumnDefault, ColumnElementColumnDefault

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin, new_uuid7, relationship


class _TestBase(Base):
    """Shares Base's registry and type map, but keeps test tables out of Base.metadata
    (and therefore out of Alembic's view of the schema)."""

    __abstract__ = True
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)


class _Owner(UUIDPrimaryKeyMixin, TimestampMixin, _TestBase):
    __tablename__ = "test_owners"

    gadgets: Mapped[list["_Gadget"]] = relationship(back_populates="owner")
    eager_gadgets: Mapped[list["_Gadget"]] = relationship(lazy="selectin", viewonly=True)


class _Gadget(UUIDPrimaryKeyMixin, _TestBase):
    __tablename__ = "test_gadgets"
    __table_args__ = (
        UniqueConstraint("serial"),
        CheckConstraint("length(serial) > 0", name="serial_not_empty"),
    )

    serial: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_owners.id"), index=True)
    seen_at: Mapped[datetime]
    owner: Mapped[_Owner] = relationship(back_populates="gadgets")


# Compiles DDL as PostgreSQL would receive it; the mock engine never connects.
_POSTGRES = create_mock_engine("postgresql+asyncpg://", executor=lambda *_: None).dialect


def _table(name: str) -> Table:
    return _TestBase.metadata.tables[name]


def _ddl(name: str) -> str:
    return str(CreateTable(_table(name)).compile(dialect=_POSTGRES))


def test_naming_convention_names_every_constraint_and_index() -> None:
    ddl = _ddl("test_gadgets")
    gadgets = _table("test_gadgets")
    (index,) = gadgets.indexes
    (unique,) = [item for item in gadgets.constraints if isinstance(item, UniqueConstraint)]
    index_ddl = str(CreateIndex(index).compile(dialect=_POSTGRES))

    assert "CONSTRAINT pk_test_gadgets PRIMARY KEY (id)" in ddl
    assert "CONSTRAINT uq_test_gadgets_serial UNIQUE (serial)" in ddl
    assert "CONSTRAINT ck_test_gadgets_serial_not_empty CHECK" in ddl
    assert "CONSTRAINT fk_test_gadgets_owner_id_test_owners FOREIGN KEY" in ddl
    assert index_ddl.startswith("CREATE INDEX ix_test_gadgets_owner_id ")
    assert list(index.columns) == [gadgets.c.owner_id]
    assert list(unique.columns) == [gadgets.c.serial]


def test_composite_uniques_sharing_a_first_column_get_distinct_names() -> None:
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    table = Table(
        "test_scores",
        metadata,
        Column("user_id", Integer),
        Column("topic_id", Integer),
        Column("week", Integer),
        UniqueConstraint("user_id", "topic_id"),
        UniqueConstraint("user_id", "week"),
    )

    ddl = str(CreateTable(table).compile(dialect=_POSTGRES))

    assert "CONSTRAINT uq_test_scores_user_id_topic_id UNIQUE (user_id, topic_id)" in ddl
    assert "CONSTRAINT uq_test_scores_user_id_week UNIQUE (user_id, week)" in ddl


def test_uuid_primary_key_is_native_uuid() -> None:
    assert "id UUID NOT NULL" in _ddl("test_owners")


def test_mixin_columns_frame_the_model_columns() -> None:
    names = [column.name for column in _table("test_owners").columns]

    assert names == ["id", "created_at", "updated_at"]
    assert next(column.name for column in _table("test_gadgets").columns) == "id"


def test_uuid_default_is_generated_in_python_as_uuid7() -> None:
    default = _table("test_owners").c.id.default

    assert isinstance(default, CallableColumnDefault)
    generated = cast("Any", default.arg)(None)  # wrapped by SQLAlchemy to take a context
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7


def test_new_uuid7_values_are_time_ordered_stdlib_uuids() -> None:
    values = [new_uuid7() for _ in range(100)]

    assert all(type(value) is uuid.UUID for value in values)
    assert values == sorted(values)


def test_timestamps_are_timestamptz_set_by_the_database() -> None:
    ddl = _ddl("test_owners")
    updated_at = _table("test_owners").c.updated_at

    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert isinstance(updated_at.onupdate, ColumnElementColumnDefault)
    assert str(updated_at.onupdate.arg) == "now()"
    assert _Owner.__mapper__.eager_defaults is True
    assert _Owner.created_at.type.timezone is True


def test_any_datetime_annotation_maps_to_timestamptz() -> None:
    assert "seen_at TIMESTAMP WITH TIME ZONE NOT NULL" in _ddl("test_gadgets")
    assert _Gadget.seen_at.type.timezone is True


def test_relationship_defaults_to_lazy_raise() -> None:
    assert _Owner.gadgets.property.lazy == "raise"
    assert _Gadget.owner.property.lazy == "raise"


def test_explicit_loader_strategy_is_kept() -> None:
    assert _Owner.eager_gadgets.property.lazy == "selectin"


def test_accessing_an_unloaded_relationship_raises_instead_of_querying() -> None:
    owner = _Owner(id=new_uuid7())
    make_transient_to_detached(owner)

    with pytest.raises(InvalidRequestError, match="lazy='raise'"):
        _ = owner.gadgets


def test_test_tables_stay_out_of_the_application_metadata() -> None:
    assert "test_owners" not in Base.metadata.tables
    assert "test_gadgets" not in Base.metadata.tables
