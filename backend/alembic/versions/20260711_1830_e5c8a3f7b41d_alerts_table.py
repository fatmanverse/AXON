"""alerts table

Revision ID: e5c8a3f7b41d
Revises: d4b7f2a61c3e
Create Date: 2026-07-11 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5c8a3f7b41d"
down_revision: str | None = "d4b7f2a61c3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=True),
        sa.Column(
            "severity",
            sa.Enum("critical", "warning", "info", name="alert_severity"),
            nullable=False,
        ),
        sa.Column("summary", sa.String(length=1024), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("firing", "resolved", name="alert_status"),
            nullable=False,
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_alerts_fingerprint"),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_alerts_fingerprint"), ["fingerprint"], unique=False)
        batch_op.create_index(batch_op.f("ix_alerts_service"), ["service"], unique=False)
        batch_op.create_index(batch_op.f("ix_alerts_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alerts_status"))
        batch_op.drop_index(batch_op.f("ix_alerts_service"))
        batch_op.drop_index(batch_op.f("ix_alerts_fingerprint"))
    op.drop_table("alerts")
