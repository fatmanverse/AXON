"""baseline —— 空基线,后续 Epic 表迁移以此为起点

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-10
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
