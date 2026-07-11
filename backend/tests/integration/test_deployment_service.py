"""T2.3 部署编排服务(DeploymentService,设计 §8.1 模式 A)。

验证 UI 触发部署的纯 async 编排核心:落一条 deployment(running, source=ui-triggered)
→ 调 PipelineAdapter.trigger 驱动 CI → 据触发结果流转 deployment 与 task 状态。
用内存 sqlite + fake adapter,不触真实 CI。

覆盖:
- 触发成功:deployment 落 running→success,task 落 success,回填 pipeline_id。
- 触发抛错:deployment 落 failed,task 落 failed,错误摘要入 task。
- 传入 previous_deployment_id 时落库(供回滚链路)。
"""

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
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter(PipelineAdapter):
    """记录 trigger 调用;可配置返回 run_id 或抛错。"""

    def __init__(self, *, run_id: str = "run-1", fail: bool = False) -> None:
        self._run_id = run_id
        self._fail = fail
        self.triggered: list[dict] = []

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        self.triggered.append({"ref": ref, "params": params})
        if self._fail:
            raise RuntimeError("ci unreachable")
        return self._run_id

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _seed_service(db, *, env=ServiceEnvironment.PROD) -> str:
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return service.id


async def _make_task(db, service_id: str) -> str:
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.DEPLOY, target=f"service:{service_id}", payload={}
        )
        return task.id


async def test_deploy_triggers_pipeline_and_marks_success(db):
    service_id = await _seed_service(db)
    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter(run_id="build-42")

    svc = DeploymentService(db, adapter_provider=lambda _svc: adapter)
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v1.2.0", strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )

    # pipeline 被触发,版本作为参数下发
    assert len(adapter.triggered) == 1
    assert adapter.triggered[0]["params"]["VERSION"] == "v1.2.0"

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.SUCCESS
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="prod"
        )
    assert len(deployments) == 1
    dep = deployments[0]
    assert dep.status == DeploymentStatus.SUCCESS
    assert dep.source == DeploymentSource.UI_TRIGGERED
    assert dep.version == "v1.2.0"
    assert dep.pipeline_id == "build-42"
    assert dep.operator == "alice"


async def test_deploy_marks_failed_when_trigger_raises(db):
    service_id = await _seed_service(db)
    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter(fail=True)

    svc = DeploymentService(db, adapter_provider=lambda _svc: adapter)
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v1.0", strategy=DeploymentStrategy.ROLLING),
        operator="bob",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="prod"
        )
    assert deployments[0].status == DeploymentStatus.FAILED


async def test_deploy_records_previous_deployment_for_rollback_chain(db):
    service_id = await _seed_service(db)
    # 先成功部署一次
    task1 = await _make_task(db, service_id)
    adapter = _FakeAdapter(run_id="b1")
    svc = DeploymentService(db, adapter_provider=lambda _svc: adapter)
    await svc.run_deploy(
        task_id=task1,
        service_id=service_id,
        request=DeployRequest(version="v1", strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )

    # 第二次部署应把上一次成功记录挂到 previous_deployment_id
    task2 = await _make_task(db, service_id)
    await svc.run_deploy(
        task_id=task2,
        service_id=service_id,
        request=DeployRequest(version="v2", strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )

    async with db.session() as session:
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="prod"
        )
    # 倒序:v2 在前
    assert deployments[0].version == "v2"
    assert deployments[0].previous_deployment_id == deployments[1].id


async def test_unknown_service_marks_task_failed(db):
    task_id = await _make_task(db, "0" * 32)
    adapter = _FakeAdapter()
    svc = DeploymentService(db, adapter_provider=lambda _svc: adapter)

    await svc.run_deploy(
        task_id=task_id,
        service_id="0" * 32,
        request=DeployRequest(version="v1", strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.FAILED
    # 未触发 CI
    assert adapter.triggered == []
