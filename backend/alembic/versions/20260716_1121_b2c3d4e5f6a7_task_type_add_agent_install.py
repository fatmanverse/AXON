"""task_type 枚举新增 AGENT_INSTALL(安装 Agent 生命周期任务)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-16 11:21:00.000000

安装 Agent 端点(POST /api/servers/{id}/install-agent)落一条 AGENT_INSTALL task,
但初版 schema 的 task_type 枚举漏了该值,导致端点在 Postgres 上写库即崩。补齐枚举值。

- Postgres:task_type 是原生枚举,需 ALTER TYPE ... ADD VALUE。该语句不能在事务块内
  执行,故用 autocommit_block 跳出 alembic 默认事务。ADD VALUE 无法回滚(Postgres
  不支持删除枚举值),downgrade 为 no-op 并在注释中说明。
- SQLite:枚举以字符串 CHECK 落地,SQLAlchemy 不生成原生类型,无需 DDL。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE 不能在事务块内运行;IF NOT EXISTS 保证重复执行安全(幂等)
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'AGENT_INSTALL'")
    # SQLite/其他:无原生枚举类型,值由应用层与 CHECK 约束保证,无需迁移


def downgrade() -> None:
    # Postgres 不支持从枚举类型中删除值;强行降级需重建类型并迁移所有引用列,
    # 风险远高于收益。此处刻意留空:AGENT_INSTALL 多存在于枚举中不影响旧代码。
    pass
