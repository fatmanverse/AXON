"""构建/制品四表 + deployments.artifact_id + env 长度修正 + task_type 加 BUILD

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-17 17:49:00.000000

「构建→部署」链路的数据地基(第一期):

- 新建 build_nodes / builds / artifact_registries / artifacts 四张表。
- deployments 加 artifact_id 软引用列(新链路填 id,旧 artifact 字符串列保留不动,
  零破坏)。
- 修正历史隐患:deployments.env / approvals.env 从 String(16) 拉齐到 String(64),
  与 environments.name / services.env 一致——自定义长环境名不再溢出截断。
- task_type 原生枚举加 BUILD(本地构建落 BUILD task)。

方言处理沿用既有范式:
- Postgres 原生枚举:新类型由 create_table 自动建;ALTER TYPE ADD VALUE 走
  autocommit_block(不能在事务内);downgrade 逆序删表后循环 drop 新枚举类型。
- SQLite:batch_alter_table 处理列变更;枚举以字符串 CHECK 落地,无原生类型。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 本迁移新增的 Postgres 原生枚举类型;downgrade 时逐一清理(先例:init 迁移)。
_NEW_ENUM_NAMES: tuple[str, ...] = (
    "build_node_status",
    "build_status",
    "build_source",
    "artifact_registry_type",
)


def _jsonb() -> sa.types.TypeEngine:
    """JSON 列:Postgres 用 JSONB,其它方言回退通用 JSON(与 base.JSONVariant 对齐)。"""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
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


def upgrade() -> None:
    bind = op.get_bind()

    # 1) build_nodes ——————————————————————————————————————————————
    op.create_table(
        "build_nodes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("server_id", sa.String(length=32), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("ssh_credential_id", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.Enum("online", "offline", "unknown", name="build_node_status"),
            nullable=False,
        ),
        sa.Column("labels", _jsonb(), nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", name="uq_build_nodes_server"),
    )
    with op.batch_alter_table("build_nodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_build_nodes_name"), ["name"], unique=True)
        batch_op.create_index(batch_op.f("ix_build_nodes_server_id"), ["server_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_build_nodes_status"), ["status"], unique=False)

    # 2) builds ——————————————————————————————————————————————————
    op.create_table(
        "builds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("repo_url", sa.String(length=512), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column("git_sha", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("build_node_id", sa.String(length=32), nullable=True),
        sa.Column("artifact_id", sa.String(length=32), nullable=True),
        sa.Column(
            "source",
            sa.Enum("ui-triggered", "pipeline-webhook", "manual", name="build_source"),
            nullable=False,
        ),
        sa.Column("pipeline_id", sa.String(length=128), nullable=True),
        sa.Column("pipeline_url", sa.String(length=512), nullable=True),
        sa.Column("operator", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "success", "failed", "canceled", name="build_status"
            ),
            nullable=False,
        ),
        sa.Column("log_url", sa.String(length=512), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_id", "service_id", "git_sha", name="uq_builds_idempotency"
        ),
    )
    with op.batch_alter_table("builds", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_builds_service_id"), ["service_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_builds_git_sha"), ["git_sha"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_builds_build_node_id"), ["build_node_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_builds_status"), ["status"], unique=False)

    # 3) artifact_registries ——————————————————————————————————————
    op.create_table(
        "artifact_registries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "type",
            sa.Enum("docker", "generic", name="artifact_registry_type"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("credential_id", sa.String(length=128), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("artifact_registries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artifact_registries_name"), ["name"], unique=True
        )

    # 4) artifacts ————————————————————————————————————————————————
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("registry_id", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=32), nullable=False),
        sa.Column("build_id", sa.String(length=32), nullable=True),
        sa.Column("git_sha", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("digest", sa.String(length=128), nullable=True),
        sa.Column("uri", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("meta", _jsonb(), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["registry_id"], ["artifact_registries.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "registry_id", "name", "version", name="uq_artifacts_registry_name_version"
        ),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artifacts_registry_id"), ["registry_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_artifacts_service_id"), ["service_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_artifacts_build_id"), ["build_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifacts_git_sha"), ["git_sha"], unique=False)

    # 5) deployments 加 artifact_id 软引用列(新链路填 id,旧 artifact 字符串列保留)
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("artifact_id", sa.String(length=32), nullable=True))

    # 5b) services 加 build_config 软配置列(照 health_check 先例,可空 JSON):承载
    #     本地构建默认(repo_url/命令/artifact_type 等)。未配置则该服务不支持本地构建。
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.add_column(sa.Column("build_config", _jsonb(), nullable=True))

    # 6) env 长度修正:deployments / approvals 从 16 拉齐到 64(消除长环境名溢出)。
    #    String 加长在 PG/SQLite 均无损。
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.alter_column(
            "env",
            existing_type=sa.String(length=16),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.alter_column(
            "env",
            existing_type=sa.String(length=16),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    # 7) task_type 加 BUILD。Postgres 原生枚举需 ALTER TYPE ADD VALUE(不能在事务内);
    #    该枚举存大写成员名(无 values_callable),故加 'BUILD'。SQLite 无需 DDL。
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'BUILD'")


def downgrade() -> None:
    bind = op.get_bind()

    # services 撤 build_config 列。
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.drop_column("build_config")

    # env 长度回退到 16。已有超 16 字符的环境名时 PG 会截断失败——与 a1b2c3d4e5f6
    # 的降级警告同理,降级前须由运维先收敛数据。
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.alter_column(
            "env",
            existing_type=sa.String(length=64),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.alter_column(
            "env",
            existing_type=sa.String(length=64),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch_op.drop_column("artifact_id")

    # 逆序删表(先删有外键的 artifacts,再删被引用的 artifact_registries)
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_git_sha"))
        batch_op.drop_index(batch_op.f("ix_artifacts_build_id"))
        batch_op.drop_index(batch_op.f("ix_artifacts_service_id"))
        batch_op.drop_index(batch_op.f("ix_artifacts_registry_id"))
    op.drop_table("artifacts")

    with op.batch_alter_table("artifact_registries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifact_registries_name"))
    op.drop_table("artifact_registries")

    with op.batch_alter_table("builds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_builds_status"))
        batch_op.drop_index(batch_op.f("ix_builds_build_node_id"))
        batch_op.drop_index(batch_op.f("ix_builds_git_sha"))
        batch_op.drop_index(batch_op.f("ix_builds_service_id"))
    op.drop_table("builds")

    with op.batch_alter_table("build_nodes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_build_nodes_status"))
        batch_op.drop_index(batch_op.f("ix_build_nodes_server_id"))
        batch_op.drop_index(batch_op.f("ix_build_nodes_name"))
    op.drop_table("build_nodes")

    # 清理本迁移新增的 Postgres 原生枚举类型(SQLite 无原生类型,drop 是 no-op)。
    # task_type 的 BUILD 值不删:PG 不支持删枚举值,多存不影响旧代码(同 AGENT_INSTALL)。
    if bind.dialect.name == "postgresql":
        for enum_name in _NEW_ENUM_NAMES:
            sa.Enum(name=enum_name).drop(bind, checkfirst=True)
