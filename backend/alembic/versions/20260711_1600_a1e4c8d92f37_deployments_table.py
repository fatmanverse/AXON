"""deployments table

Revision ID: a1e4c8d92f37
Revises: 32c979e5d2d4
Create Date: 2026-07-11 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1e4c8d92f37"
down_revision: str | None = "32c979e5d2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("env", sa.String(length=16), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("artifact", sa.String(length=512), nullable=True),
        sa.Column(
            "strategy",
            sa.Enum(
                "rolling", "canary", "blue-green", "recreate", name="deployment_strategy"
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "ui-triggered", "pipeline-webhook", "manual", name="deployment_source"
            ),
            nullable=False,
        ),
        sa.Column("pipeline_id", sa.String(length=128), nullable=True),
        sa.Column("pipeline_url", sa.String(length=512), nullable=True),
        sa.Column("operator", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running", "success", "failed", "rolled_back", name="deployment_status"
            ),
            nullable=False,
        ),
        sa.Column("previous_deployment_id", sa.String(length=32), nullable=True),
        sa.Column("scan_result_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deployments_service_id"), ["service_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_deployments_env"), ["env"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_deployments_git_sha"), ["git_sha"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_deployments_status"), ["status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployments_status"))
        batch_op.drop_index(batch_op.f("ix_deployments_git_sha"))
        batch_op.drop_index(batch_op.f("ix_deployments_env"))
        batch_op.drop_index(batch_op.f("ix_deployments_service_id"))
    op.drop_table("deployments")
    # 显式清理 PostgreSQL 上遗留的 Enum 类型(sqlite 无此类型,忽略)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="deployment_strategy").drop(bind, checkfirst=True)
        sa.Enum(name="deployment_source").drop(bind, checkfirst=True)
        sa.Enum(name="deployment_status").drop(bind, checkfirst=True)
