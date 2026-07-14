"""环境仓储 CRUD 契约(自定义环境管理)。

覆盖:创建、按 name 取、name 唯一约束、列表(按 name 排序)、删除。
环境是 services/servers 的 env 段真相源,无预置数据。
"""

import pytest

from app.core.db import Database
from app.core.errors import AppError
from app.models.base import Base
from app.schemas.environment import EnvironmentCreate
from app.services.environment_repository import EnvironmentRepository


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_create_and_get_by_name(db):
    payload = EnvironmentCreate(
        name="prod", display_name="生产", requires_approval=True, description="生产环境"
    )
    async with db.session() as session:
        created = await EnvironmentRepository(session).create(payload)
        assert created.name == "prod"
        assert created.requires_approval is True

    async with db.session() as session:
        found = await EnvironmentRepository(session).get_by_name("prod")
        assert found is not None
        assert found.display_name == "生产"


async def test_get_by_name_returns_none_when_absent(db):
    async with db.session() as session:
        assert await EnvironmentRepository(session).get_by_name("missing") is None


async def test_duplicate_name_rejected(db):
    payload = EnvironmentCreate(name="dev")
    async with db.session() as session:
        await EnvironmentRepository(session).create(payload)

    async with db.session() as session:
        with pytest.raises(AppError, match="已存在"):
            await EnvironmentRepository(session).create(EnvironmentCreate(name="dev"))


async def test_list_sorted_by_name(db):
    async with db.session() as session:
        repo = EnvironmentRepository(session)
        await repo.create(EnvironmentCreate(name="staging"))
        await repo.create(EnvironmentCreate(name="dev"))
        await repo.create(EnvironmentCreate(name="prod"))

    async with db.session() as session:
        rows = await EnvironmentRepository(session).list()
        assert [r.name for r in rows] == ["dev", "prod", "staging"]


async def test_delete_environment(db):
    async with db.session() as session:
        created = await EnvironmentRepository(session).create(EnvironmentCreate(name="temp"))
        env_id = created.id

    async with db.session() as session:
        await EnvironmentRepository(session).delete(env_id)

    async with db.session() as session:
        assert await EnvironmentRepository(session).get_by_name("temp") is None


async def test_delete_missing_raises(db):
    async with db.session() as session:
        with pytest.raises(AppError, match="环境不存在"):
            await EnvironmentRepository(session).delete("nonexistent")
