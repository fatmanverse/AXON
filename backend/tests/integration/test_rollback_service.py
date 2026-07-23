"""定向回滚编排：CI/artifact 双路径与状态闭环。"""

from dataclasses import replace

import pytest_asyncio

from app.core.db import Database
from app.models.artifact import ArtifactRegistryType
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.service import ServiceCreate
from app.services.artifact_deployment_service import ArtifactDeployInput
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.triggered: list[dict] = []

    async def trigger(self, ref, *, params):
        self.triggered.append({"ref": ref, "params": params})
        if self.fail:
            raise RuntimeError("ci down")
        return "rollback-run-1"


class _FakeArtifactDeployer:
    def __init__(self, artifact_id: str, *, fail: bool = False) -> None:
        self.fail = fail
        self.resolve_calls: list[tuple[str, str]] = []
        self.deploy_calls: list[tuple[str, str]] = []
        self.input = ArtifactDeployInput(
            service_id="",
            artifact_id=artifact_id,
            version="v1-artifact",
            git_sha="sha-artifact",
            uri="registry/app:v1-artifact",
            registry_type=ArtifactRegistryType.DOCKER,
        )

    async def resolve(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        self.resolve_calls.append((service_id, artifact_id))
        return replace(self.input, service_id=service_id)

    async def deploy(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        self.deploy_calls.append((service_id, artifact_id))
        if self.fail:
            raise RuntimeError("runtime down")
        return replace(self.input, service_id=service_id)


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
        task = await TaskRepository(session).create_deployment_operation(
            type=TaskType.ROLLBACK,
            service_id=service_id,
            payload={},
        )
        return task.id


async def _seed_success(
    db,
    service_id: str,
    *,
    version: str,
    artifact: str,
    artifact_id: str | None = None,
    git_sha: str | None = None,
    scan_result_id: str | None = None,
    previous_deployment_id: str | None = None,
) -> str:
    async with db.session() as session:
        repo = DeploymentRepository(session)
        deployment = await repo.create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version=version,
            artifact=artifact,
            artifact_id=artifact_id,
            git_sha=git_sha,
            scan_result_id=scan_result_id,
            previous_deployment_id=previous_deployment_id,
        )
        await repo.mark_status(deployment.id, DeploymentStatus.SUCCESS)
        return deployment.id


async def test_ci_rollback_deploys_selected_history_and_closes_current(db):
    service_id = await _seed_service(db)
    target_id = await _seed_success(
        db,
        service_id,
        version="v1",
        artifact="registry/app:v1",
        git_sha="sha-v1",
        scan_result_id="scan-v1",
    )
    current_id = await _seed_success(
        db,
        service_id,
        version="v2",
        artifact="registry/app:v2",
        previous_deployment_id=target_id,
    )
    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter()

    await DeploymentService(db, adapter_provider=lambda _service: adapter).run_rollback(
        task_id=task_id,
        service_id=service_id,
        target_deployment_id=target_id,
        operator="alice",
    )

    assert adapter.triggered == [
        {
            "ref": "v1",
            "params": {"ARTIFACT": "registry/app:v1", "ENV": "prod", "VERSION": "v1"},
        }
    ]
    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    new_deployment = rows[0]
    assert task.status == TaskStatus.SUCCESS
    assert new_deployment.status == DeploymentStatus.SUCCESS
    assert new_deployment.previous_deployment_id == current_id
    assert new_deployment.version == "v1"
    assert new_deployment.artifact == "registry/app:v1"
    assert new_deployment.git_sha == "sha-v1"
    assert new_deployment.scan_result_id == "scan-v1"
    assert new_deployment.pipeline_id == "rollback-run-1"
    assert next(row for row in rows if row.id == current_id).status == DeploymentStatus.ROLLED_BACK


async def test_artifact_rollback_uses_artifact_owner_without_ci(db):
    service_id = await _seed_service(db)
    artifact_id = "a" * 32
    target_id = await _seed_success(
        db,
        service_id,
        version="v1-artifact",
        artifact="registry/app:v1-artifact",
        artifact_id=artifact_id,
        git_sha="sha-artifact",
    )
    current_id = await _seed_success(
        db,
        service_id,
        version="v2",
        artifact="registry/app:v2",
        previous_deployment_id=target_id,
    )
    task_id = await _make_task(db, service_id)
    artifact_deployer = _FakeArtifactDeployer(artifact_id)

    def _unexpected_ci(_service):
        raise AssertionError("artifact rollback must not resolve CI adapter")

    await DeploymentService(
        db,
        adapter_provider=_unexpected_ci,
        artifact_deployer=artifact_deployer,
    ).run_rollback(
        task_id=task_id,
        service_id=service_id,
        target_deployment_id=target_id,
        operator="bob",
    )

    assert artifact_deployer.resolve_calls == [(service_id, artifact_id)]
    assert artifact_deployer.deploy_calls == [(service_id, artifact_id)]
    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    new_deployment = rows[0]
    assert task.status == TaskStatus.SUCCESS
    assert new_deployment.artifact_id == artifact_id
    assert new_deployment.artifact == "registry/app:v1-artifact"
    assert new_deployment.previous_deployment_id == current_id
    assert next(row for row in rows if row.id == current_id).status == DeploymentStatus.ROLLED_BACK


async def test_artifact_rollback_failure_marks_new_record_failed_and_keeps_current(db):
    service_id = await _seed_service(db)
    artifact_id = "b" * 32
    target_id = await _seed_success(
        db,
        service_id,
        version="v1",
        artifact="registry/app:v1",
        artifact_id=artifact_id,
    )
    current_id = await _seed_success(
        db,
        service_id,
        version="v2",
        artifact="registry/app:v2",
        previous_deployment_id=target_id,
    )
    task_id = await _make_task(db, service_id)

    await DeploymentService(
        db,
        adapter_provider=lambda _service: _FakeAdapter(),
        artifact_deployer=_FakeArtifactDeployer(artifact_id, fail=True),
    ).run_rollback(
        task_id=task_id,
        service_id=service_id,
        target_deployment_id=target_id,
        operator="carol",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    assert task.status == TaskStatus.FAILED
    assert rows[0].status == DeploymentStatus.FAILED
    assert next(row for row in rows if row.id == current_id).status == DeploymentStatus.SUCCESS


async def test_ci_rollback_failure_marks_new_record_failed_and_keeps_current(db):
    service_id = await _seed_service(db)
    target_id = await _seed_success(
        db,
        service_id,
        version="v1",
        artifact="registry/app:v1",
    )
    current_id = await _seed_success(
        db,
        service_id,
        version="v2",
        artifact="registry/app:v2",
        previous_deployment_id=target_id,
    )
    task_id = await _make_task(db, service_id)

    await DeploymentService(
        db,
        adapter_provider=lambda _service: _FakeAdapter(fail=True),
    ).run_rollback(
        task_id=task_id,
        service_id=service_id,
        target_deployment_id=target_id,
        operator="dave",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        rows = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    assert task.status == TaskStatus.FAILED
    assert rows[0].status == DeploymentStatus.FAILED
    assert next(row for row in rows if row.id == current_id).status == DeploymentStatus.SUCCESS
