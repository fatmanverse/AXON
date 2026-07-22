"""构建能力仓储层验收(build / build_node / artifact 三仓储)。

用内存 sqlite 建全表,验证:
- BuildRepository:create 落 pending+started_at;get 不存在抛 404;mark_status 经
  状态机守卫(非法流转抛 ValueError,终态盖 finished_at);set_artifact 回填;
  list_for_service 倒序限量。
- BuildNodeRepository:ensure_local_node 幂等(重复调返回同一条本地节点);
  create/list/delete/get。
- ArtifactRepository:ensure_default_registry 幂等(取/建唯一 is_default generic 库);
  create_artifact 落制品;list_for_service。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.models.artifact import ArtifactRegistryType
from app.models.base import Base
from app.models.build import BuildSource, BuildStatus
from app.services.artifact_repository import ArtifactRepository
from app.services.build_node_repository import BuildNodeRepository
from app.services.build_repository import BuildRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


# ── BuildRepository ────────────────────────────────────────────────


async def test_build_create_defaults_pending_and_started(db):
    async with db.session() as session:
        build = await BuildRepository(session).create(
            service_id="svc1", source=BuildSource.UI_TRIGGERED, git_ref="main"
        )
        assert build.status == BuildStatus.PENDING
        assert build.started_at is not None
        assert build.git_ref == "main"


async def test_build_get_missing_raises_404(db):
    async with db.session() as session:
        with pytest.raises(Exception) as excinfo:
            await BuildRepository(session).get("nope")
        assert getattr(excinfo.value, "status_code", None) == 404


async def test_build_mark_status_advances_and_stamps_finished(db):
    async with db.session() as session:
        repo = BuildRepository(session)
        build = await repo.create(service_id="svc1", source=BuildSource.UI_TRIGGERED)
        await repo.mark_status(build.id, BuildStatus.RUNNING)
        done = await repo.mark_status(build.id, BuildStatus.SUCCESS)
        assert done.status == BuildStatus.SUCCESS
        assert done.finished_at is not None


async def test_build_mark_status_rejects_illegal_transition(db):
    async with db.session() as session:
        repo = BuildRepository(session)
        build = await repo.create(service_id="svc1", source=BuildSource.UI_TRIGGERED)
        await repo.mark_status(build.id, BuildStatus.RUNNING)  # pending→running ok
        await repo.mark_status(build.id, BuildStatus.SUCCESS)  # running→success ok
        with pytest.raises(ValueError):
            await repo.mark_status(build.id, BuildStatus.RUNNING)  # terminal→running 非法


async def test_build_set_artifact_backfills(db):
    async with db.session() as session:
        repo = BuildRepository(session)
        build = await repo.create(service_id="svc1", source=BuildSource.UI_TRIGGERED)
        updated = await repo.set_artifact(build.id, "art123")
        assert updated.artifact_id == "art123"


async def test_build_list_for_service_desc_limited(db):
    async with db.session() as session:
        repo = BuildRepository(session)
        for _ in range(3):
            await repo.create(service_id="svc1", source=BuildSource.UI_TRIGGERED)
        await repo.create(service_id="other", source=BuildSource.UI_TRIGGERED)
        rows = await repo.list_for_service("svc1", limit=2)
        assert len(rows) == 2


# ── BuildNodeRepository ────────────────────────────────────────────


async def test_ensure_local_node_is_idempotent(db):
    async with db.session() as session:
        repo = BuildNodeRepository(session)
        first = await repo.ensure_local_node()
        second = await repo.ensure_local_node()
        assert first.id == second.id
        assert first.server_id is None
        nodes = await repo.list()
        assert len(nodes) == 1


async def test_build_node_delete(db):
    async with db.session() as session:
        repo = BuildNodeRepository(session)
        node = await repo.ensure_local_node()
        await repo.delete(node.id)
        assert await repo.list() == []


# ── ArtifactRepository ─────────────────────────────────────────────


async def test_ensure_default_registry_idempotent(db):
    async with db.session() as session:
        repo = ArtifactRepository(session)
        first = await repo.ensure_default_registry(ArtifactRegistryType.GENERIC)
        second = await repo.ensure_default_registry(ArtifactRegistryType.GENERIC)
        assert first.id == second.id
        assert first.is_default is True


async def test_create_artifact_and_list_for_service(db):
    async with db.session() as session:
        repo = ArtifactRepository(session)
        registry = await repo.ensure_default_registry(ArtifactRegistryType.GENERIC)
        await repo.create_artifact(
            registry_id=registry.id,
            service_id="svc1",
            name="app",
            version="1.0.0",
            uri="/var/lib/axon/artifacts/app-1.0.0.tar.gz",
            git_sha="a" * 40,
            size_bytes=2048,
        )
        rows = await repo.list_for_service("svc1")
        assert len(rows) == 1
        assert rows[0].uri.endswith("app-1.0.0.tar.gz")


async def test_get_artifact_found(db):
    async with db.session() as session:
        repo = ArtifactRepository(session)
        registry = await repo.ensure_default_registry(ArtifactRegistryType.GENERIC)
        artifact = await repo.create_artifact(
            registry_id=registry.id,
            service_id="svc1",
            name="app",
            version="2.0.0",
            uri="/tmp/app-2.0.0.tar.gz",
        )
        artifact_id = artifact.id

    async with db.session() as session:
        fetched = await ArtifactRepository(session).get_artifact(artifact_id)
    assert fetched.id == artifact_id
    assert fetched.service_id == "svc1"


async def test_get_artifact_missing_raises_404(db):
    from app.core.errors import AppError

    async with db.session() as session:
        repo = ArtifactRepository(session)
        with pytest.raises(AppError) as exc_info:
            await repo.get_artifact("00000000000000000000000000000000")
    assert exc_info.value.status_code == 404
