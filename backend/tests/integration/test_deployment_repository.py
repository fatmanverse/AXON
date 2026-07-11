"""T2.1 deployments 仓储(§14.3)。

用内存 sqlite 验证:创建(默认 running)、按 id 取、按 service+env 分页列表
(按创建时间倒序)、受状态机守卫的流转、查最近一次成功部署(供回滚取上一版)。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus, DeploymentStrategy
from app.services.deployment_repository import DeploymentRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _create(repo, **kwargs):
    defaults = {
        "service_id": "svc1",
        "env": "prod",
        "source": DeploymentSource.UI_TRIGGERED,
        "git_sha": "abc123",
        "version": "v1.0.0",
    }
    defaults.update(kwargs)
    return await repo.create(**defaults)


async def test_create_defaults_to_running(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await _create(repo)

    assert dep.id
    assert dep.status == DeploymentStatus.RUNNING
    assert dep.source == DeploymentSource.UI_TRIGGERED
    assert dep.strategy == DeploymentStrategy.ROLLING
    assert dep.started_at is not None


async def test_get_returns_created(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await _create(repo)
        dep_id = dep.id

    async with db.session() as session:
        fetched = await DeploymentRepository(session).get(dep_id)
    assert fetched.id == dep_id
    assert fetched.git_sha == "abc123"


async def test_get_missing_raises_404(db):
    from app.core.errors import AppError

    async with db.session() as session:
        with pytest.raises(AppError) as exc:
            await DeploymentRepository(session).get("0" * 32)
    assert exc.value.status_code == 404


async def test_list_filters_by_service_and_env_newest_first(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        await _create(repo, service_id="svc1", env="prod", version="v1")
        await _create(repo, service_id="svc1", env="prod", version="v2")
        await _create(repo, service_id="svc1", env="dev", version="d1")
        await _create(repo, service_id="svc2", env="prod", version="o1")

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")

    versions = [r.version for r in rows]
    assert versions == ["v2", "v1"]  # 倒序:最新在前


async def test_mark_status_enforces_state_machine(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await _create(repo)
        dep_id = dep.id

    async with db.session() as session:
        repo = DeploymentRepository(session)
        updated = await repo.mark_status(dep_id, DeploymentStatus.SUCCESS)
        assert updated.status == DeploymentStatus.SUCCESS
        assert updated.finished_at is not None

    # 终态不可再转出
    async with db.session() as session:
        repo = DeploymentRepository(session)
        with pytest.raises(ValueError, match="非法状态流转"):
            await repo.mark_status(dep_id, DeploymentStatus.RUNNING)


async def test_latest_successful_returns_most_recent_success(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        old = await _create(repo, service_id="svc1", env="prod", version="v1")
        await repo.mark_status(old.id, DeploymentStatus.SUCCESS)
        new = await _create(repo, service_id="svc1", env="prod", version="v2")
        await repo.mark_status(new.id, DeploymentStatus.SUCCESS)
        # 一条失败的不应被选中
        bad = await _create(repo, service_id="svc1", env="prod", version="v3")
        await repo.mark_status(bad.id, DeploymentStatus.FAILED)
        new_id = new.id

    async with db.session() as session:
        latest = await DeploymentRepository(session).latest_successful("svc1", env="prod")
    assert latest is not None
    assert latest.id == new_id


async def test_latest_successful_none_when_no_success(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await _create(repo)
        await repo.mark_status(dep.id, DeploymentStatus.FAILED)

    async with db.session() as session:
        latest = await DeploymentRepository(session).latest_successful("svc1", env="prod")
    assert latest is None
