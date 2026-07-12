"""环境晋升编排验收(T2.16,设计 §10.3)。

晋升 = 取源环境(staging)最近一次成功部署的 artifact,在目标环境(prod)以
**同一制品**重新部署(不重构建)。覆盖:
- 成功晋升:目标环境落一条新 deployment,artifact/version/git_sha 与源一致。
- 源环境无成功部署:明确失败(无制品可晋升)。
- 目标环境无对应 service(同名不同 env):明确失败。
- 全程不抛:结果落在 deployment 与 task 状态上。

用内存 sqlite + fake adapter,不触真实 CI。
"""

from __future__ import annotations

import pytest_asyncio

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
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


class _FakeAdapter(PipelineAdapter):
    def __init__(self) -> None:
        self.triggered: list[dict] = []

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        self.triggered.append({"ref": ref, "params": params})
        return "promo-run-1"

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


async def _seed(db, *, name: str, env: ServiceEnvironment) -> str:
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name=name,
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": f"{name}.service"},
            )
        )
        return service.id


async def _seed_success_deploy(db, service_id, env, *, artifact, version, git_sha):
    """在指定 service 上直接落一条 success 部署(模拟 staging 已验证的制品)。"""
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env=env,
            source=DeploymentSource.PIPELINE_WEBHOOK,
            version=version,
            artifact=artifact,
            git_sha=git_sha,
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)


async def _make_task(db, service_id):
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.DEPLOY, target=f"service:{service_id}"
        )
        return task.id


async def test_promote_reuses_staging_artifact_in_prod(db):
    staging_id = await _seed(db, name="billing", env=ServiceEnvironment.STAGING)
    prod_id = await _seed(db, name="billing", env=ServiceEnvironment.PROD)
    await _seed_success_deploy(
        db, staging_id, "staging", artifact="reg/billing:abc123", version="v1.4.0", git_sha="abc123"
    )
    task_id = await _make_task(db, prod_id)

    adapter = _FakeAdapter()
    svc = DeploymentService(db, adapter_provider=lambda _s: adapter)
    await svc.run_promotion(
        task_id=task_id,
        source_service_id=staging_id,
        target_service_id=prod_id,
        operator="alice",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.SUCCESS
        deployments = await DeploymentRepository(session).list_for_service(
            prod_id, env="prod"
        )
    # prod 落了同一制品(不重构建)
    assert len(deployments) == 1
    assert deployments[0].artifact == "reg/billing:abc123"
    assert deployments[0].version == "v1.4.0"
    assert deployments[0].git_sha == "abc123"
    assert deployments[0].status == DeploymentStatus.SUCCESS


async def test_promote_without_source_success_fails(db):
    staging_id = await _seed(db, name="billing", env=ServiceEnvironment.STAGING)
    prod_id = await _seed(db, name="billing", env=ServiceEnvironment.PROD)
    task_id = await _make_task(db, prod_id)

    adapter = _FakeAdapter()
    svc = DeploymentService(db, adapter_provider=lambda _s: adapter)
    await svc.run_promotion(
        task_id=task_id,
        source_service_id=staging_id,
        target_service_id=prod_id,
        operator="alice",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.FAILED
    # 未触发 CI(无制品可晋升)
    assert adapter.triggered == []
