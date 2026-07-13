"""HIGH-4 验收(T3.4,§9/§14.9):部署时按 git_sha 回填 scan_result_id。

审计发现:deployments.scan_result_id 是永不写入的死列——详情页靠 git_sha 现查代偿,
但产出明确要求"部署时按 git_sha 回填"。本测试证明:带 git_sha 且该 sha 已有扫描
结果时,新建 deployment 的 scan_result_id 指向该扫描记录;无扫描结果时留空。

service 级测试,fake pipeline,不触真实 CI/网络。
"""

import uuid

import pytest_asyncio

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.db import Database
from app.models.base import Base
from app.models.scan_result import Scanner
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskType
from app.schemas.service import ServiceCreate
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService, DeployRequest
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref, *, params):
        return f"run-{uuid.uuid4().hex[:8]}"

    async def get_status(self, ref, *, run_id):
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


async def _seed_service(db) -> str:
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.STAGING,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return service.id


async def _make_task(db, service_id) -> str:
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.DEPLOY, target=f"service:{service_id}", payload={}
        )
        return task.id


async def _deploy(db, service_id, *, git_sha) -> None:
    task_id = await _make_task(db, service_id)
    svc = DeploymentService(db, adapter_provider=lambda _s: _FakeAdapter())
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v1", strategy="rolling", git_sha=git_sha),
        operator="alice",
    )


async def test_backfills_scan_result_id_when_scan_exists(db):
    service_id = await _seed_service(db)
    async with db.session() as session:
        scan = await ScanResultRepository(session).upsert(
            service="billing",
            git_sha="abc123",
            scanner=Scanner.SEMGREP,
            critical=0,
            passed=True,
        )
        scan_id = scan.id

    await _deploy(db, service_id, git_sha="abc123")

    async with db.session() as session:
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="staging"
        )
    assert len(deployments) == 1
    assert deployments[0].scan_result_id == scan_id


async def test_scan_result_id_empty_when_no_scan(db):
    service_id = await _seed_service(db)
    await _deploy(db, service_id, git_sha="no-scan-sha")

    async with db.session() as session:
        deployments = await DeploymentRepository(session).list_for_service(
            service_id, env="staging"
        )
    assert len(deployments) == 1
    assert deployments[0].scan_result_id is None
