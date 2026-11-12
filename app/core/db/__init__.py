"""Database layer: engine, sessions, model base.

Services import `AsyncSession` / `AsyncEngine` from here, never from SQLAlchemy:
import-linter forbids services from importing sqlalchemy directly.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db.base import (
    NAMING_CONVENTION,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    new_uuid7,
    relationship,
)
from app.core.db.engine import create_engine, create_session_factory
from app.core.db.session import get_session

__all__ = [
    "NAMING_CONVENTION",
    "AsyncEngine",
    "AsyncSession",
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "create_engine",
    "create_session_factory",
    "get_session",
    "new_uuid7",
    "relationship",
]
