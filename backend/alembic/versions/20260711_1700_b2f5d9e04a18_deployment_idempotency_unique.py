"""deployment idempotency unique constraint

Revision ID: b2f5d9e04a18
Revises: a1e4c8d92f37
Create Date: 2026-07-11 17:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2f5d9e04a18"
down_revision: str | None = "a1e4c8d92f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_deployments_idempotency", ["pipeline_id", "service_id", "env"]
        )


def downgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_constraint("uq_deployments_idempotency", type_="unique")
