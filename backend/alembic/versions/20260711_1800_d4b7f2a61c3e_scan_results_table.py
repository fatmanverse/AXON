"""scan_results table

Revision ID: d4b7f2a61c3e
Revises: c3a6e1f5b209
Create Date: 2026-07-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4b7f2a61c3e"
down_revision: str | None = "c3a6e1f5b209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "scanner",
            sa.Enum("sonarqube", "semgrep", "trivy", name="scan_scanner"),
            nullable=False,
        ),
        sa.Column("critical", sa.Integer(), nullable=False),
        sa.Column("high", sa.Integer(), nullable=False),
        sa.Column("medium", sa.Integer(), nullable=False),
        sa.Column("low", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("report_url", sa.String(length=512), nullable=True),
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
        sa.UniqueConstraint("git_sha", "scanner", name="uq_scan_results_idempotency"),
    )
    with op.batch_alter_table("scan_results", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scan_results_service"), ["service"], unique=False)
        batch_op.create_index(batch_op.f("ix_scan_results_git_sha"), ["git_sha"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("scan_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scan_results_git_sha"))
        batch_op.drop_index(batch_op.f("ix_scan_results_service"))
    op.drop_table("scan_results")
