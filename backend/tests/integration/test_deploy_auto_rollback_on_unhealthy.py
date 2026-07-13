"""HIGH-1 验收(T3.8,§11.2):发布后健康检查失败时自动回滚。

审计发现:健康检查失败路径只 mark FAILED,产出明确要求的"失败自动触发自动回滚"
未接。本测试覆盖:开关开启且健康检查失败时,重部署上一版已知good制品(生成新的
成功 deployment,previous 指向被回滚版),被回滚的失败版留 FAILED;开关关闭时只
mark FAILED、不回滚。

service 级测试,fake pipeline + 必失败 checker,不触真实 CI/网络。
"""

import uuid

import pytest_asyncio

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.db import Database
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus, DeploymentStrategy
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.service import ServiceCreate
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService, DeployRequest
from app.services.health_checker import HealthResult
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        # 每次触发返回唯一 run_id(真实 CI 亦然),避免多次部署撞
        # UNIQUE(pipeline_id, service, env)——与被测逻辑无关的测试构造噪声。
        return f"build-{uuid.uuid4().hex}"

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


class _StubChecker:
    def __init__(self, *, healthy: bool) -> None:
        self._healthy = healthy

    async def check(self, config) -> HealthResult:
        return HealthResult(healthy=self._healthy, attempts=1, detail="stub")


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
                env=ServiceEnvironment.STAGING,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
                health_check={"type": "http", "url": "http://x/health"},
            )
        )
        return service.id


async def _make_deploy_task(db, service_id: str) -> str:
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.DEPLOY, target=f"service:{service_id}", payload={}
        )
        return task.id


async def _deploy_success(db, service_id: str, version: str) -> None:
    """先跑一次健康的部署,留下一版成功制品供回滚取用。"""
    task_id = await _make_deploy_task(db, service_id)
    svc = DeploymentService(
        db,
        adapter_provider=lambda _s: _FakeAdapter(),
        health_checker=_StubChecker(healthy=True),
    )
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version=version, strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )


async def test_unhealthy_triggers_auto_rollback_when_enabled(db):
    service_id = await _seed_service(db)
    await _deploy_success(db, service_id, "v1")  # 上一版成功制品

    # 第二次部署健康检查失败,开关开启 → 自动回滚到 v1。共享一个 adapter 实例,
    # 使 trigger_count 递增出不同 pipeline_id(真实 CI 每次 run_id 不同),避免
    # v2 与回滚重部署撞 UNIQUE(pipeline_id, service, env)。
    task_id = await _make_deploy_task(db, service_id)
    adapter = _FakeAdapter()
    svc = DeploymentService(
        db,
        adapter_provider=lambda _s: adapter,
        health_checker=_StubChecker(healthy=False),
        auto_rollback_on_health_fail=True,
    )
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v2", strategy=DeploymentStrategy.ROLLING),
        operator="bob",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.FAILED  # 本次部署仍判失败
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="staging"
        )

    statuses = [d.status for d in deployments]
    # 存在一条自动回滚生成的成功记录(重部署 v1)
    assert DeploymentStatus.SUCCESS in statuses
    # 健康失败的 v2 记录落 FAILED
    v2 = next(d for d in deployments if d.version == "v2")
    assert v2.status == DeploymentStatus.FAILED
    # 自动回滚记录:最新一条是成功的重部署,版本为 v1
    latest = deployments[0]
    assert latest.status == DeploymentStatus.SUCCESS
    assert latest.version == "v1"
    assert latest.source == DeploymentSource.UI_TRIGGERED


async def test_unhealthy_no_rollback_when_disabled(db):
    service_id = await _seed_service(db)
    await _deploy_success(db, service_id, "v1")

    task_id = await _make_deploy_task(db, service_id)
    adapter = _FakeAdapter()
    svc = DeploymentService(
        db,
        adapter_provider=lambda _s: adapter,
        health_checker=_StubChecker(healthy=False),
        auto_rollback_on_health_fail=False,  # 默认关闭
    )
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v2", strategy=DeploymentStrategy.ROLLING),
        operator="bob",
    )

    async with db.session() as session:
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="staging"
        )
    # 只有 v1(成功)+ v2(失败),无自动回滚生成的额外成功记录
    assert len(deployments) == 2
    v2 = next(d for d in deployments if d.version == "v2")
    assert v2.status == DeploymentStatus.FAILED
