"""services and service placements

Revision ID: 32c979e5d2d4
Revises: 7dcf0b0aeb93
Create Date: 2026-07-11 13:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "32c979e5d2d4"
down_revision: str | None = "7dcf0b0aeb93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "env",
            sa.Enum("dev", "staging", "prod", name="service_environment"),
            nullable=False,
        ),
        sa.Column(
            "runtime",
            sa.Enum("k8s", "docker", "systemd", "process", "cloud-fn", name="service_runtime"),
            nullable=False,
        ),
        sa.Column(
            "runtime_ref",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("desired_version", sa.String(length=128), nullable=True),
        sa.Column("current_deployment_id", sa.String(length=32), nullable=True),
        sa.Column(
            "reload_mode",
            sa.Enum("reload", "restart", name="service_reload_mode"),
            nullable=False,
        ),
        sa.Column(
            "health_check",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "env", name="uq_services_name_env"),
    )
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_services_env"), ["env"], unique=False)
        batch_op.create_index(batch_op.f("ix_services_name"), ["name"], unique=False)
        batch_op.create_index(batch_op.f("ix_services_runtime"), ["runtime"], unique=False)

    op.create_table(
        "service_placements",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("server_id", sa.String(length=32), nullable=True),
        sa.Column("observed_version", sa.String(length=128), nullable=True),
        sa.Column(
            "observed_status",
            sa.Enum("running", "stopped", "error", "unknown", name="placement_observed_status"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "server_id", name="uq_service_placements_service_server"),
    )
    with op.batch_alter_table("service_placements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_service_placements_observed_status"), ["observed_status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_service_placements_server_id"), ["server_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_service_placements_service_id"), ["service_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("service_placements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_service_placements_service_id"))
        batch_op.drop_index(batch_op.f("ix_service_placements_server_id"))
        batch_op.drop_index(batch_op.f("ix_service_placements_observed_status"))
    op.drop_table("service_placements")

    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_services_runtime"))
        batch_op.drop_index(batch_op.f("ix_services_name"))
        batch_op.drop_index(batch_op.f("ix_services_env"))
    op.drop_table("services")
