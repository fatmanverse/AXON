"""T2.6 service_configs 版本仓储(§12.1/§14.5)。

用内存 sqlite 验证:
- 首个版本 version=1 且自动成为 current。
- 后续版本 version 按 service 自增,新版接管 current,旧版 is_current 置 False(互斥)。
- version 自增按 service 独立(不同 service 各自从 1 起)。
- 列版本倒序、取 current、取指定版本。
- activate 切换 current(配置回滚):把旧 current 置 False、目标版置 True。
- 取不存在的服务当前配置返回 None。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.service_config import ConfigFormat
from app.services.service_config_repository import ServiceConfigRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_first_version_is_one_and_current(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        cfg = await repo.create_version(
            service_id="svc1", content="A=1", format=ConfigFormat.ENV, created_by="alice"
        )
        assert cfg.version == 1
        assert cfg.is_current is True


async def test_second_version_increments_and_takes_current(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        first = await repo.create_version(service_id="svc1", content="A=1")
        second = await repo.create_version(service_id="svc1", content="A=2")
        assert second.version == 2
        assert second.is_current is True
        # 旧版失去 current
        refreshed_first = await repo.get_version("svc1", first.version)
        assert refreshed_first.is_current is False


async def test_version_increments_per_service(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        await repo.create_version(service_id="svc1", content="A=1")
        other = await repo.create_version(service_id="svc2", content="B=1")
        # svc2 独立从 1 起
        assert other.version == 1


async def test_list_versions_newest_first(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        await repo.create_version(service_id="svc1", content="A=1")
        await repo.create_version(service_id="svc1", content="A=2")
        await repo.create_version(service_id="svc1", content="A=3")
        versions = await repo.list_versions("svc1")
        assert [v.version for v in versions] == [3, 2, 1]


async def test_get_current_returns_latest_active(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        await repo.create_version(service_id="svc1", content="A=1")
        await repo.create_version(service_id="svc1", content="A=2")
        current = await repo.get_current("svc1")
        assert current is not None
        assert current.version == 2
        assert current.content == "A=2"


async def test_get_current_none_when_no_config(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        assert await repo.get_current("ghost") is None


async def test_activate_switches_current_for_rollback(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        v1 = await repo.create_version(service_id="svc1", content="A=1")
        await repo.create_version(service_id="svc1", content="A=2")  # v2 current
        # 回滚到 v1
        activated = await repo.activate("svc1", v1.version)
        assert activated.version == 1
        assert activated.is_current is True
        current = await repo.get_current("svc1")
        assert current.version == 1
        # v2 不再 current
        v2 = await repo.get_version("svc1", 2)
        assert v2.is_current is False


async def test_activate_missing_version_raises(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        await repo.create_version(service_id="svc1", content="A=1")
        with pytest.raises(Exception) as exc:
            await repo.activate("svc1", 99)
        assert "不存在" in str(exc.value)


async def test_get_version_missing_raises(db):
    async with db.session() as session:
        repo = ServiceConfigRepository(session)
        with pytest.raises(Exception) as exc:
            await repo.get_version("svc1", 1)
        assert "不存在" in str(exc.value)
