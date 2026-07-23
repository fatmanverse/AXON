"""init schema —— 统一运维控制面全量基线(整合原多份增量迁移)

Revision ID: 0001_init_schema
Revises:
Create Date: 2026-07-13 00:00:00.000000

包含表:tasks / roles / users / role_permissions / user_roles / audit_logs /
servers / services / service_placements / deployments / service_configs /
scan_results / alerts / config_deliveries / approvals。
原先按 Epic 拆分的增量迁移(含 deployments 幂等唯一约束、service_configs.target_path)
已在此处一次性建成最终形态。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_init_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> sa.types.TypeEngine:
    """跨方言 JSON 列:PostgreSQL 用 JSONB,其余方言回退到通用 JSON。"""
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    """所有表统一的 created_at / updated_at 时间戳列。"""
    return (
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
    )


# 全部具名 Enum 类型,downgrade 时在 PostgreSQL 上显式清理
_ENUM_NAMES: tuple[str, ...] = (
    "task_type",
    "task_status",
    "audit_result",
    "server_access_mode",
    "agent_status",
    "service_environment",
    "service_runtime",
    "service_reload_mode",
    "placement_observed_status",
    "deployment_strategy",
    "deployment_source",
    "deployment_status",
    "service_config_format",
    "scan_scanner",
    "alert_severity",
    "alert_status",
    "config_delivery_status",
    "approval_action",
    "approval_status",
)


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "DEPLOY",
                "ROLLBACK",
                "START",
                "STOP",
                "DELETE",
                "RESTART",
                "UPDATE_CONFIG",
                name="task_type",
            ),
            nullable=False,
        ),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("payload", _jsonb(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "SUCCESS", "FAILED", "UNKNOWN", name="task_status"),
            nullable=False,
        ),
        sa.Column("result", _jsonb(), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tasks_status"), ["status"], unique=False)

    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_roles_name"), ["name"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission", name="uq_role_permission"),
    )
    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_role_permissions_role_id"), ["role_id"], unique=False)

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("result", sa.Enum("SUCCESS", "FAILED", name="audit_result"), nullable=False),
        sa.Column("before", _jsonb(), nullable=True),
        sa.Column("after", _jsonb(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("ua", sa.String(length=512), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_actor"), ["actor"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_env"), ["env"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_target"), ["target"], unique=False)

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
        sa.Column("labels", _jsonb(), nullable=False),
        *_timestamp_columns(),
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
        sa.Column("runtime_ref", _jsonb(), nullable=False),
        sa.Column("desired_version", sa.String(length=128), nullable=True),
        sa.Column("current_deployment_id", sa.String(length=32), nullable=True),
        sa.Column(
            "reload_mode",
            sa.Enum("reload", "restart", name="service_reload_mode"),
            nullable=False,
        ),
        sa.Column("health_check", _jsonb(), nullable=True),
        *_timestamp_columns(),
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

    # deployments:含原 b2f5d9e04a18 的幂等唯一约束(pipeline_id, service_id, env)
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
            sa.Enum("rolling", "canary", "blue-green", "recreate", name="deployment_strategy"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("ui-triggered", "pipeline-webhook", "manual", name="deployment_source"),
            nullable=False,
        ),
        sa.Column("pipeline_id", sa.String(length=128), nullable=True),
        sa.Column("pipeline_url", sa.String(length=512), nullable=True),
        sa.Column("operator", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "success", "failed", "rolled_back", name="deployment_status"),
            nullable=False,
        ),
        sa.Column("previous_deployment_id", sa.String(length=32), nullable=True),
        sa.Column("scan_result_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_id", "service_id", "env", name="uq_deployments_idempotency"
        ),
    )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deployments_service_id"), ["service_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_deployments_env"), ["env"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployments_git_sha"), ["git_sha"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployments_status"), ["status"], unique=False)

    # service_configs:含原 f6d9b3a72e14 追加的 target_path 列(推模式下发落点)
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
        sa.Column("target_path", sa.String(length=512), nullable=True),
        *_timestamp_columns(),
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
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("git_sha", "scanner", name="uq_scan_results_idempotency"),
    )
    with op.batch_alter_table("scan_results", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scan_results_service"), ["service"], unique=False)
        batch_op.create_index(batch_op.f("ix_scan_results_git_sha"), ["git_sha"], unique=False)

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
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_alerts_fingerprint"),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_alerts_fingerprint"), ["fingerprint"], unique=False)
        batch_op.create_index(batch_op.f("ix_alerts_service"), ["service"], unique=False)
        batch_op.create_index(batch_op.f("ix_alerts_status"), ["status"], unique=False)

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
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(["config_id"], ["service_configs.id"], ondelete="CASCADE"),
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
        *_timestamp_columns(),
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

    with op.batch_alter_table("config_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_config_deliveries_status"))
        batch_op.drop_index(batch_op.f("ix_config_deliveries_placement_id"))
        batch_op.drop_index(batch_op.f("ix_config_deliveries_config_id"))
    op.drop_table("config_deliveries")

    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alerts_status"))
        batch_op.drop_index(batch_op.f("ix_alerts_service"))
        batch_op.drop_index(batch_op.f("ix_alerts_fingerprint"))
    op.drop_table("alerts")

    with op.batch_alter_table("scan_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scan_results_git_sha"))
        batch_op.drop_index(batch_op.f("ix_scan_results_service"))
    op.drop_table("scan_results")

    with op.batch_alter_table("service_configs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_service_configs_is_current"))
        batch_op.drop_index(batch_op.f("ix_service_configs_service_id"))
    op.drop_table("service_configs")

    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployments_status"))
        batch_op.drop_index(batch_op.f("ix_deployments_git_sha"))
        batch_op.drop_index(batch_op.f("ix_deployments_env"))
        batch_op.drop_index(batch_op.f("ix_deployments_service_id"))
    op.drop_table("deployments")

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

    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_servers_name"))
        batch_op.drop_index(batch_op.f("ix_servers_host"))
        batch_op.drop_index(batch_op.f("ix_servers_agent_status"))
    op.drop_table("servers")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_target"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_env"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_actor"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_action"))
    op.drop_table("audit_logs")

    op.drop_table("user_roles")

    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_permissions_role_id"))
    op.drop_table("role_permissions")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
    op.drop_table("users")

    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roles_name"))
    op.drop_table("roles")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_status"))
    op.drop_table("tasks")

    # 显式清理 PostgreSQL 上遗留的具名 Enum 类型(sqlite 无此类型,忽略)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_name in _ENUM_NAMES:
            sa.Enum(name=enum_name).drop(bind, checkfirst=True)
