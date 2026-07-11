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
            service_id="svc1", env="prod", pipeline_id="p-1",
            status=DeploymentStatus.SUCCESS,
        )
        await repo.upsert_from_webhook(
            service_id="svc1", env="prod", pipeline_id="p-2",
            status=DeploymentStatus.SUCCESS,
        )

    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service("svc1", env="prod")
    assert len(rows) == 2
