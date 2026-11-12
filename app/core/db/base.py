"""Declarative base, relationship default and mixins shared by every model.

Rules for models:
- Subclass `Base`; its metadata carries the constraint naming convention, so
  Alembic can name (and later drop) every constraint and index deterministically.
- Declare relationships with `app.core.db.relationship`, never with
  `sqlalchemy.orm.relationship` directly: it defaults to `lazy="raise"`, so an
  accidental lazy load (an N+1 in waiting) fails loudly. Load related rows
  explicitly with `selectinload` / `joinedload`.
- Use `UUIDPrimaryKeyMixin` and `TimestampMixin` for the plan's standard columns.
"""

import functools
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

import uuid_utils
from sqlalchemy import DateTime, MetaData, func, orm
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    # All columns: composite uniques that share a first column must not collide.
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every `Mapped[datetime]` column is `timestamptz`; naive timestamps are not used.
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


def _lazy_raise_by_default[**P, R](factory: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(factory)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        kwargs.setdefault("lazy", "raise")
        return factory(*args, **kwargs)

    return wrapper


# The single sanctioned use of the original; ruff bans it everywhere else.
relationship = _lazy_raise_by_default(orm.relationship)  # noqa: TID251  # the wrapper itself
"""`sqlalchemy.orm.relationship` with `lazy="raise"` unless a loader is given.

SQLAlchemy has no global default for `lazy`, hence this wrapper; it keeps the
original signature for type checkers.
"""


def new_uuid7() -> uuid.UUID:
    """A time-ordered UUIDv7 as a stdlib `uuid.UUID` (what the ORM and asyncpg expect)."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)


class UUIDPrimaryKeyMixin:
    """`id uuid PRIMARY KEY`, generated in Python as UUIDv7 when the row is flushed."""

    # sort_order: `id` is the first column in generated tables, timestamps the last.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid7, sort_order=-1)


class TimestampMixin:
    """`created_at` / `updated_at` as `timestamptz NOT NULL`, set by the database.

    `eager_defaults` makes INSERT and UPDATE return both values (RETURNING), so they
    are loaded without a later implicit SELECT, which an async session cannot run.
    A model that declares its own `__mapper_args__` must keep `eager_defaults=True`.
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=1)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=1
    )
