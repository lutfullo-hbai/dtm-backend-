"""Empty initial revision: proves the pipeline.

No schema change. It exists so upgrade, downgrade and `alembic check` run
end to end before the first real table arrives.

Revision ID: c22591b23c3c
Revises: (base)
Create Date: 2026-11-12 08:32:17.621584+00:00
"""

from collections.abc import Sequence

revision: str = "c22591b23c3c"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
