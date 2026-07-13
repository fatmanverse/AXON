"""T2.4 webhook 幂等 upsert + 乱序保护(设计 §8.3 ②③)。

验证 DeploymentRepository.upsert_from_webhook:
- 首次上报:INSERT 一条记录。
- 同 (pipeline_id, service, env) 重复上报:UPDATE 同一条,不新增(幂等)。
- 乱序保护:较旧 finished_at 的事件不覆盖较新状态(running 晚于 success 到达时丢弃)。
- 状态直接落终态(webhook 上报的是已知结局,不走 running→ 中转)。
"""

import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.deployment import DeploymentStatus
from app.services.deployment_repository import DeploymentRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_first_report_inserts(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-100",
            status=DeploymentStatus.SUCCESS,
            git_sha="abc",
            version="v1",
            artifact="reg/x:abc",
            operator="ci",
        )
        assert dep.status == DeploymentStatus.SUCCESS
        assert dep.pipeline_id == "p-100"

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")
    assert len(rows) == 1


async def test_duplicate_report_updates_same_row(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-100",
            status=DeploymentStatus.RUNNING,
            version="v1",
        )
    async with db.session() as session:
        repo = DeploymentRepository(session)
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-100",
            status=DeploymentStatus.SUCCESS,
            version="v1",
        )

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")
    # 幂等:同一 (pipeline_id, service, env) 只有一条,状态被更新为 success
    assert len(rows) == 1
    assert rows[0].status == DeploymentStatus.SUCCESS


async def test_out_of_order_older_event_does_not_overwrite(db):
    from datetime import UTC, datetime

    newer = datetime(2026, 7, 11, 12, 0, 30, tzinfo=UTC)
    older = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)

    async with db.session() as session:
        repo = DeploymentRepository(session)
        # 先到的是较新的 success 事件
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-1",
            status=DeploymentStatus.SUCCESS,
            finished_at=newer,
        )
    async with db.session() as session:
        repo = DeploymentRepository(session)
        # 后到的是较旧的 running 事件(重试乱序),应被丢弃
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-1",
            status=DeploymentStatus.RUNNING,
            finished_at=older,
        )

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")
    assert rows[0].status == DeploymentStatus.SUCCESS  # 未被旧事件覆盖


async def test_distinct_pipeline_ids_create_separate_rows(db):
    async with db.session() as session:
        repo = DeploymentRepository(session)
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-1",
            status=DeploymentStatus.SUCCESS,
        )
        await repo.upsert_from_webhook(
            service_id="svc1",
            env="prod",
            pipeline_id="p-2",
            status=DeploymentStatus.SUCCESS,
        )

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")
    assert len(rows) == 2


async def test_insert_conflict_falls_back_to_update(db):
    """竞态回退分支确定性覆盖(§8.3 ②):find 在竞态窗口内看到 None、INSERT 撞唯一
    约束时,回退为 find+update,不新增、不抛 IntegrityError。

    sqlite 内存库是单连接,无法忠实模拟两连接并发 savepoint(savepoint 是连接级的),
    故用打桩让首次 find_by_idempotency 返回 None(模拟"检查时对方还没提交"的竞态
    窗口),此后 INSERT 必撞已存在的同幂等键约束,走回退分支。生产 asyncpg 每 session
    独立连接,savepoint 回退按预期工作。
    """
    # 先落一条真实记录(制造"库里已存在同幂等键"的事实)
    async with db.session() as session:
        await DeploymentRepository(session).upsert_from_webhook(
            service_id="svc-d",
            env="prod",
            pipeline_id="p-x",
            status=DeploymentStatus.RUNNING,
            version="v1",
        )

    # 新 session:打桩让"首发检查"看到 None,强制走 INSERT→撞约束→回退 update
    async with db.session() as session:
        repo = DeploymentRepository(session)
        real_find = repo.find_by_idempotency
        calls = {"n": 0}

        async def _racy_find(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # 模拟竞态窗口:对方尚未提交
            return await real_find(**kwargs)

        repo.find_by_idempotency = _racy_find  # type: ignore[method-assign]

        dep = await repo.upsert_from_webhook(
            service_id="svc-d",
            env="prod",
            pipeline_id="p-x",
            status=DeploymentStatus.SUCCESS,
            version="v1",
        )
        # 回退后拿到的是原记录并更新了状态,而非抛错或新增
        assert dep.status == DeploymentStatus.SUCCESS
        assert calls["n"] == 2  # 首次 None(快路径未命中)+ 回退里再查一次

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc-d", env="prod")
    assert len(rows) == 1  # 未新增,收敛为一条
