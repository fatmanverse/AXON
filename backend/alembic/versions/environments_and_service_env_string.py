"""environments 表 + services.env 由枚举改为字符串(自定义环境管理)

Revision ID: a1b2c3d4e5f6
Revises: f35b1607cae0
Create Date: 2026-07-13 17:00:00.000000

环境改为用户自建(§10.1 环境模型的动态化):
- 新增 environments 表:环境是否存在、是否需要审批(requires_approval)的唯一真相源。
- services.env 从具名枚举 service_environment(dev/staging/prod)改为纯 String,
  可承载任意已创建的环境名。Postgres 上需 USING env::text 转换,并 drop 掉不再使用
  的 service_environment 枚举类型;SQLite 无原生枚举,batch 重建列即可。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f35b1607cae0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    # 1) environments 表
    op.create_table(
        "environments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_environments_name"), ["name"], unique=True)

    # 2) services.env 枚举 → String。Postgres 需显式 USING 转换文本;其余方言直接改列类型。
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "services",
            "env",
            existing_type=postgresql.ENUM(
                "dev", "staging", "prod", name="service_environment"
            ),
            type_=sa.String(length=64),
            existing_nullable=False,
            postgresql_using="env::text",
        )
        # 列已不再引用该枚举类型,清理之(仅 Postgres 有独立枚举类型对象)
        sa.Enum(name="service_environment").drop(bind, checkfirst=True)
    else:
        with op.batch_alter_table("services", schema=None) as batch_op:
            batch_op.alter_column(
                "env",
                existing_type=sa.Enum(
                    "dev", "staging", "prod", name="service_environment"
                ),
                type_=sa.String(length=64),
                existing_nullable=False,
            )

    # 3) servers.environment:服务器归属环境(需求2)。nullable——既有服务器无归属,
    #    新纳管走 API 层软校验必填;引用 environments.name(软引用,不建外键,与
    #    services.env 的字符串引用一致)。加索引便于按环境过滤/列表。
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("environment", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_servers_environment"), ["environment"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_servers_environment"))
        batch_op.drop_column("environment")

    # 还原 services.env 为具名枚举。已存在的自定义环境名超出 dev/staging/prod 时,
    # Postgres 的 USING env::service_environment 会失败——这是刻意的:降级到只支持
    # 三值枚举前,须先把 env 收敛回合法枚举值(数据迁移由运维在降级前处理)。
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        service_environment = postgresql.ENUM(
            "dev", "staging", "prod", name="service_environment"
        )
        service_environment.create(bind, checkfirst=True)
        op.alter_column(
            "services",
            "env",
            existing_type=sa.String(length=64),
            type_=service_environment,
            existing_nullable=False,
            postgresql_using="env::service_environment",
        )
    else:
        with op.batch_alter_table("services", schema=None) as batch_op:
            batch_op.alter_column(
                "env",
                existing_type=sa.String(length=64),
                type_=sa.Enum(
                    "dev", "staging", "prod", name="service_environment"
                ),
                existing_nullable=False,
            )

    with op.batch_alter_table("environments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_environments_name"))
    op.drop_table("environments")
