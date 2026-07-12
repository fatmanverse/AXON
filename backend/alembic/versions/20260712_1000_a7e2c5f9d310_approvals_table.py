"""approvals table

Revision ID: a7e2c5f9d310
Revises: f6d9b3a72e14
Create Date: 2026-07-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7e2c5f9d310"
down_revision: str | None = "f6d9b3a72e14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("env", sa.String(length=16), nullable=False),
        sa.Column(
            "action",
            sa.Enum("deploy", "delete", "rollback", name="approval_action"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="approval_status"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
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
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approvals_service_id"), ["service_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_approvals_env"), ["env"], unique=False)
        batch_op.create_index(batch_op.f("ix_approvals_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approvals_status"))
        batch_op.drop_index(batch_op.f("ix_approvals_env"))
        batch_op.drop_index(batch_op.f("ix_approvals_service_id"))
    op.drop_table("approvals")
    # 显式清理 PostgreSQL 遗留 Enum 类型(sqlite 无此类型,忽略)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="approval_action").drop(bind, checkfirst=True)
        sa.Enum(name="approval_status").drop(bind, checkfirst=True)
