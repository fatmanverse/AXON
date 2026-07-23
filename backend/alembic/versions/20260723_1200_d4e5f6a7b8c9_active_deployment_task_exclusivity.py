"""active deployment task exclusivity

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIVE_DEPLOYMENT_TASKS = sa.text(
    "type IN ('DEPLOY', 'ROLLBACK') AND status IN ('PENDING', 'RUNNING')"
)


def upgrade() -> None:
    op.create_index(
        "uq_tasks_active_deployment_target",
        "tasks",
        ["target"],
        unique=True,
        postgresql_where=_ACTIVE_DEPLOYMENT_TASKS,
        sqlite_where=_ACTIVE_DEPLOYMENT_TASKS,
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_active_deployment_target", table_name="tasks")
