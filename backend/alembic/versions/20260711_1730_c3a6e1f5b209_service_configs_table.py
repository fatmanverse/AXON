"""service_configs table

Revision ID: c3a6e1f5b209
Revises: b2f5d9e04a18
Create Date: 2026-07-11 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3a6e1f5b209"
down_revision: str | None = "b2f5d9e04a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_configs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "format",
            sa.Enum("env", "yaml", "properties", "json", name="service_config_format"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("comment", sa.String(length=512), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint("service_id", "version", name="uq_service_configs_service_version"),
    )
    with op.batch_alter_table("service_configs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_service_configs_service_id"), ["service_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_service_configs_is_current"), ["is_current"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("service_configs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_service_configs_is_current"))
        batch_op.drop_index(batch_op.f("ix_service_configs_service_id"))
    op.drop_table("service_configs")
