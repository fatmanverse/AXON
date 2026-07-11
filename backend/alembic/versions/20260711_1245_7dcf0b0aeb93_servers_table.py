"""servers table

Revision ID: 7dcf0b0aeb93
Revises: 5ff8e7715573
Create Date: 2026-07-11 12:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7dcf0b0aeb93"
down_revision: str | None = "5ff8e7715573"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "servers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column(
            "access_mode",
            sa.Enum("ssh", "agent", name="server_access_mode"),
            nullable=False,
        ),
        sa.Column("ssh_credential_id", sa.String(length=128), nullable=True),
        sa.Column("agent_id", sa.String(length=128), nullable=True),
        sa.Column(
            "agent_status",
            sa.Enum("online", "offline", "unknown", name="agent_status"),
            nullable=False,
        ),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column(
            "labels",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "(access_mode = 'ssh' AND ssh_credential_id IS NOT NULL AND agent_id IS NULL) "
            "OR (access_mode = 'agent' AND ssh_credential_id IS NULL AND agent_id IS NOT NULL)",
            name="ck_servers_access_mode_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id"),
    )
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_servers_agent_status"), ["agent_status"], unique=False)
        batch_op.create_index(batch_op.f("ix_servers_host"), ["host"], unique=True)
        batch_op.create_index(batch_op.f("ix_servers_name"), ["name"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_servers_name"))
        batch_op.drop_index(batch_op.f("ix_servers_host"))
        batch_op.drop_index(batch_op.f("ix_servers_agent_status"))
    op.drop_table("servers")
