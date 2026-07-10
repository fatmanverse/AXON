"""Alembic 迁移环境:异步引擎 + 从应用配置注入 URL + 复用声明式 metadata。"""

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  确保 Base.metadata 收集到全部表(后续 Epic 逐步补充)
from alembic import context
from app.core.config import get_settings
from app.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用应用配置里的 DB URL 覆盖 alembic.ini 的空值
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """修正 autogenerate 对 JSONB 的渲染:默认会产出未命名空间的 `Text()`,
    导致迁移文件 import 缺失、在 PG 上执行报错。这里只渲染 JSONB 本身(带 sa.Text()
    与 import),外层的 `JSON().with_variant(...)` 仍交给 alembic 默认逻辑包裹,
    避免二次包裹。返回 False 走默认渲染。
    """
    from sqlalchemy.dialects import postgresql

    if type_ == "type" and isinstance(obj, postgresql.JSONB):
        autogen_context.imports.add("import sqlalchemy as sa")
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.JSONB(astext_type=sa.Text())"
    return False


def run_migrations_offline() -> None:
    """离线模式:仅按 URL 生成 SQL,不建立连接。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式:异步引擎连库执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
