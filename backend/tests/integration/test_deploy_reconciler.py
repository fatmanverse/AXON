"""部署轮询兜底(补偿通道)验收(T2.7,设计 §8.2/§8.3④)。

webhook 是 at-least-once,但也可能整条丢失(CI 未配上报步骤、网络长时间中断)。
轮询兜底定时扫描仍卡在 running 的 deployment,用 PipelineAdapter.get_status 查其
pipeline 当前状态,补齐终态(running→success/failed)。与 webhook 靠幂等键去重:
两者都经 DeploymentRepository 的状态机,只前进不回退,重复补齐不会翻状态。

覆盖:
- running + pipeline 已成功 → 补成 success。
- running + pipeline 已失败 → 补成 failed。
- running + pipeline 仍在跑 → 保持 running(不动)。
- 无 pipeline_id 的 running(UI 触发未回填)→ 跳过,不查 CI。
- 已是终态的 deployment → 不查、不动。

用内存 sqlite + fake adapter,不触真实 CI。
"""

from __future__ import annotations

import pytest_asyncio

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.db import Database
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.deploy_reconciler import DeployReconciler
from app.services.deployment_repository import DeploymentRepository
from app.services.service_repository import ServiceRepository


class _FakeAdapter(PipelineAdapter):
    """按 run_id 返回预设状态的假适配器,记录被查询的 run_id。"""

    def __init__(self, statuses: dict[str, PipelineRunStatus]) -> None:
        self._statuses = statuses
        self.queried: list[str] = []

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str | None:
        return None

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        self.queried.append(run_id)
        return self._statuses.get(run_id, PipelineRunStatus.UNKNOWN)

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _seed_service(db) -> str:
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return service.id


async def _running_deploy(db, service_id, *, pipeline_id: str | None) -> str:
    async with db.session() as session:
        dep = await DeploymentRepository(session).create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version="v1",
            pipeline_id=pipeline_id,
        )
        return dep.id


async def test_reconcile_running_to_success(db):
    service_id = await _seed_service(db)
    dep_id = await _running_deploy(db, service_id, pipeline_id="run-1")
    adapter = _FakeAdapter({"run-1": PipelineRunStatus.SUCCESS})

    reconciler = DeployReconciler(db, adapter_provider=lambda _s: adapter)
    result = await reconciler.reconcile_once()

    assert result.reconciled == 1
    async with db.session() as session:
        dep = await DeploymentRepository(session).get(dep_id)
    assert dep.status == DeploymentStatus.SUCCESS
    assert adapter.queried == ["run-1"]


async def test_reconcile_running_to_failed(db):
    service_id = await _seed_service(db)
    dep_id = await _running_deploy(db, service_id, pipeline_id="run-2")
    adapter = _FakeAdapter({"run-2": PipelineRunStatus.FAILED})

    reconciler = DeployReconciler(db, adapter_provider=lambda _s: adapter)
    await reconciler.reconcile_once()

    async with db.session() as session:
        dep = await DeploymentRepository(session).get(dep_id)
    assert dep.status == DeploymentStatus.FAILED


async def test_reconcile_leaves_still_running(db):
    service_id = await _seed_service(db)
    dep_id = await _running_deploy(db, service_id, pipeline_id="run-3")
    adapter = _FakeAdapter({"run-3": PipelineRunStatus.RUNNING})

    reconciler = DeployReconciler(db, adapter_provider=lambda _s: adapter)
    result = await reconciler.reconcile_once()

    assert result.reconciled == 0
    async with db.session() as session:
        dep = await DeploymentRepository(session).get(dep_id)
    assert dep.status == DeploymentStatus.RUNNING


async def test_reconcile_skips_running_without_pipeline_id(db):
    service_id = await _seed_service(db)
    await _running_deploy(db, service_id, pipeline_id=None)
    adapter = _FakeAdapter({})

    reconciler = DeployReconciler(db, adapter_provider=lambda _s: adapter)
    result = await reconciler.reconcile_once()

    # 无 pipeline_id 无从查 CI,跳过,不触发 adapter
    assert result.reconciled == 0
    assert adapter.queried == []


async def test_reconcile_ignores_terminal_deployments(db):
    service_id = await _seed_service(db)
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.PIPELINE_WEBHOOK,
            version="v1",
            pipeline_id="run-4",
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)
    adapter = _FakeAdapter({"run-4": PipelineRunStatus.FAILED})

    reconciler = DeployReconciler(db, adapter_provider=lambda _s: adapter)
    result = await reconciler.reconcile_once()

    # 终态不查、不动(幂等键去重:只前进不回退)
    assert result.reconciled == 0
    assert adapter.queried == []
    async with db.session() as session:
        dep = await DeploymentRepository(session).get(dep.id)
    assert dep.status == DeploymentStatus.SUCCESS
