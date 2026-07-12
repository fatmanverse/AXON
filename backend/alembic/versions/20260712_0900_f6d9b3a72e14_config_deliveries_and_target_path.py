"""config_deliveries table + service_configs.target_path

Revision ID: f6d9b3a72e14
Revises: e5c8a3f7b41d
Create Date: 2026-07-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6d9b3a72e14"
down_revision: str | None = "e5c8a3f7b41d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 配置写到目标机的绝对路径(推模式下发落点,§12.2);历史行可空
    with op.batch_alter_table("service_configs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_path", sa.String(length=512), nullable=True))

    op.create_table(
        "config_deliveries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("config_id", sa.String(length=32), nullable=False),
        sa.Column("placement_id", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "success", "failed", name="config_delivery_status"),
            nullable=False,
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["config_id"], ["service_configs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["placement_id"], ["service_placements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("config_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_config_deliveries_config_id"), ["config_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_config_deliveries_placement_id"), ["placement_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_config_deliveries_status"), ["status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("config_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_config_deliveries_status"))
        batch_op.drop_index(batch_op.f("ix_config_deliveries_placement_id"))
        batch_op.drop_index(batch_op.f("ix_config_deliveries_config_id"))
    op.drop_table("config_deliveries")
    with op.batch_alter_table("service_configs", schema=None) as batch_op:
        batch_op.drop_column("target_path")
    # PostgreSQL 上显式清理 Enum 类型(sqlite 无此类型)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="config_delivery_status").drop(bind, checkfirst=True)
