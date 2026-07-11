"""T2.5 回滚编排(DeploymentService.run_rollback,设计 §11.1/§11.2)。

回滚 = 重部署上一版制品,不是撤销:
- 取当前运行版(最近 success)的 artifact 作为重部署目标,生成新 deployment
  (source=ui-triggered),新记录 previous_deployment_id 指向被回滚的当前版。
- 被回滚的当前版 status 落 rolled_back(闭环)。
- 无可回滚版本时 task 落 failed,不产生新记录。
"""

import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.service import ServiceCreate
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter:
    def __init__(self):
        self.triggered = []

    async def trigger(self, ref, *, params):
        self.triggered.append({"ref": ref, "params": params})
        return "rollback-run-1"

    async def get_status(self, ref, *, run_id):
        from app.adapters.pipeline import PipelineRunStatus

        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref, *, run_id):
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
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return svc.id


async def _make_task(db, service_id) -> str:
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.ROLLBACK, target=f"service:{service_id}", payload={}
        )
        return task.id


async def _seed_success(db, service_id, *, version, artifact) -> str:
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version=version,
            artifact=artifact,
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)
        return dep.id


async def test_rollback_redeploys_current_artifact_and_closes_loop(db):
    service_id = await _seed_service(db)
    await _seed_success(db, service_id, version="v1", artifact="registry/app:v1")
    current_id = await _seed_success(db, service_id, version="v2", artifact="registry/app:v2")
    task_id = await _make_task(db, service_id)

    adapter = _FakeAdapter()
    svc = DeploymentService(db, adapter_provider=lambda _s: adapter)
    await svc.run_rollback(task_id=task_id, service_id=service_id, operator="alice")

    # CI 被触发,重部署当前版的 artifact
    assert len(adapter.triggered) == 1
    assert adapter.triggered[0]["params"]["ARTIFACT"] == "registry/app:v2"

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.SUCCESS
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")

    # 新记录在前,previous 指向被回滚的当前版
    new_dep = rows[0]
    assert new_dep.status == DeploymentStatus.SUCCESS
    assert new_dep.previous_deployment_id == current_id
    assert new_dep.artifact == "registry/app:v2"

    # 被回滚的当前版落 rolled_back
    rolled = next(r for r in rows if r.id == current_id)
    assert rolled.status == DeploymentStatus.ROLLED_BACK


async def test_rollback_without_success_marks_task_failed(db):
    service_id = await _seed_service(db)
    task_id = await _make_task(db, service_id)

    adapter = _FakeAdapter()
    svc = DeploymentService(db, adapter_provider=lambda _s: adapter)
    await svc.run_rollback(task_id=task_id, service_id=service_id, operator="bob")

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.FAILED
    assert adapter.triggered == []


async def test_rollback_marks_failed_when_ci_raises(db):
    service_id = await _seed_service(db)
    await _seed_success(db, service_id, version="v1", artifact="registry/app:v1")
    task_id = await _make_task(db, service_id)

    class _BoomAdapter(_FakeAdapter):
        async def trigger(self, ref, *, params):
            raise RuntimeError("ci down")

    svc = DeploymentService(db, adapter_provider=lambda _s: _BoomAdapter())
    await svc.run_rollback(task_id=task_id, service_id=service_id, operator="carol")

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    assert task.status == TaskStatus.FAILED
    # 回滚生成的新记录落 failed;原成功版不被误闭环
    assert rows[0].status == DeploymentStatus.FAILED
