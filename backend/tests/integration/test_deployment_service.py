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
        deployments = await DeploymentRepository(session).list_for_service(service_id, env="prod")
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
        deployments = await DeploymentRepository(session).list_for_service(service_id, env="prod")
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
        deployments = await DeploymentRepository(session).list_for_service(service_id, env="prod")
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


async def test_rollout_provider_executes_strategy_after_ci(db):
    """注入 rollout_provider 时,CI 触发成功后按策略铺开(此处 k8s rolling→rollout restart)。"""
    from app.models.service import Runtime
    from app.services.release_strategy import RolloutContext

    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="k8s-svc",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.K8S,
                runtime_ref={"namespace": "prod", "workload": "k8s-svc"},
            )
        )
        service_id = service.id

    calls: list[tuple] = []

    class _FakeK8s:
        async def restart(self, namespace: str, workload: str) -> None:
            calls.append(("restart", namespace, workload))

        async def scale(self, namespace: str, workload: str, replicas: int) -> None:
            calls.append(("scale", namespace, workload, replicas))

    async def _rollout_provider(svc):
        return RolloutContext(
            runtime=Runtime.K8S,
            k8s_adapter=_FakeK8s(),
            namespace=svc.runtime_ref["namespace"],
            workload=svc.runtime_ref["workload"],
            replicas=2,
        )

    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter(run_id="r1")
    svc = DeploymentService(
        db,
        adapter_provider=lambda _svc: adapter,
        rollout_provider=_rollout_provider,
    )
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v1", strategy=DeploymentStrategy.ROLLING),
        operator="alice",
    )

    # CI 触发了,且策略铺开执行了 k8s rollout restart
    assert adapter.triggered
    assert calls == [("restart", "prod", "k8s-svc")]
    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.SUCCESS


async def test_rollout_strategy_failure_marks_deploy_failed(db):
    """策略铺开失败(如 canary 未支持)→ deployment 与 task 落 failed。"""
    from app.models.service import Runtime
    from app.services.release_strategy import RolloutContext

    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="k8s-canary",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.K8S,
                runtime_ref={"namespace": "prod", "workload": "k8s-canary"},
            )
        )
        service_id = service.id

    class _FakeK8s:
        async def restart(self, namespace: str, workload: str) -> None:
            pass

        async def scale(self, namespace: str, workload: str, replicas: int) -> None:
            pass

    async def _rollout_provider(svc):
        return RolloutContext(
            runtime=Runtime.K8S,
            k8s_adapter=_FakeK8s(),
            namespace="prod",
            workload="k8s-canary",
            replicas=2,
        )

    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter(run_id="r1")
    svc = DeploymentService(
        db,
        adapter_provider=lambda _svc: adapter,
        rollout_provider=_rollout_provider,
    )
    # canary 在 k8s 原生不支持 → 策略执行抛 501 → 落 failed
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v1", strategy=DeploymentStrategy.CANARY),
        operator="alice",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
        deployments = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    assert task.status == TaskStatus.FAILED
    assert deployments[0].status == DeploymentStatus.FAILED


# ── artifact 直接部署测试 ─────────────────────────────────────────────────────


class _FakeArtifactDeployer:
    """记录 deploy 调用；可配置失败。"""
    def __init__(self, *, artifact_id: str, version: str = "v1.0.0",
                 uri: str = "/tmp/app.tar.gz", fail: bool = False) -> None:
        from app.models.artifact import ArtifactRegistryType
        from app.services.artifact_deployment_service import ArtifactDeployInput
        self._input = ArtifactDeployInput(
            service_id="",  # filled in test
            artifact_id=artifact_id,
            version=version,
            git_sha=None,
            uri=uri,
            registry_type=ArtifactRegistryType.GENERIC,
        )
        self._fail = fail
        self.deploy_calls: list[tuple[str, str]] = []

    async def deploy(self, service_id: str, artifact_id: str):
        self.deploy_calls.append((service_id, artifact_id))
        if self._fail:
            raise RuntimeError("runtime error")
        from dataclasses import replace
        return replace(self._input, service_id=service_id)


async def test_artifact_deploy_success_saves_artifact_id(db):
    """artifact 模式：deployment 保存 artifact_id/uri/version；CI 未调用。"""
    from app.models.deployment import DeploymentStatus
    from app.models.task import TaskStatus

    service_id = await _seed_service(db, env=ServiceEnvironment.DEV)
    task_id = await _make_task(db, service_id)
    fake_deployer = _FakeArtifactDeployer(
        artifact_id="a" * 32, version="v1.0.0", uri="/tmp/app-v1.0.0.tar.gz"
    )
    ci_adapter = _FakeAdapter()
    svc = DeploymentService(
        db,
        adapter_provider=lambda _: ci_adapter,
        artifact_deployer=fake_deployer,
    )

    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(artifact_id="a" * 32, strategy=DeploymentStrategy.ROLLING),
        operator="bob",
    )

    # CI adapter 不应被触发
    assert ci_adapter.triggered == []

    # artifact_deployer.deploy 被调用一次
    assert fake_deployer.deploy_calls == [(service_id, "a" * 32)]

    # task 落 success
    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.SUCCESS

    # deployment 有 artifact_id
    async with db.session() as session:
        from app.services.deployment_repository import DeploymentRepository
        deps = await DeploymentRepository(session).list_for_service(service_id)
    assert len(deps) == 1
    assert deps[0].artifact_id == "a" * 32
    assert deps[0].status == DeploymentStatus.SUCCESS


async def test_artifact_deploy_runtime_failure_marks_failed(db):
    """artifact runtime 失败 → deployment + task 均落 failed。"""
    from app.models.task import TaskStatus

    service_id = await _seed_service(db, env=ServiceEnvironment.DEV)
    task_id = await _make_task(db, service_id)
    fake_deployer = _FakeArtifactDeployer(artifact_id="b" * 32, fail=True)
    svc = DeploymentService(
        db,
        adapter_provider=lambda _: _FakeAdapter(),
        artifact_deployer=fake_deployer,
    )

    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(artifact_id="b" * 32),
        operator="alice",
    )

    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.FAILED


async def test_ci_deploy_still_works_without_artifact_id(db):
    """旧 CI 模式（version）不受 artifact 分支影响。"""
    from app.models.task import TaskStatus

    service_id = await _seed_service(db)
    task_id = await _make_task(db, service_id)
    adapter = _FakeAdapter(run_id="ci-run-99")

    svc = DeploymentService(db, adapter_provider=lambda _: adapter)
    await svc.run_deploy(
        task_id=task_id,
        service_id=service_id,
        request=DeployRequest(version="v2.0.0"),
        operator="alice",
    )

    assert len(adapter.triggered) == 1
    async with db.session() as session:
        task = await TaskRepository(session).get(task_id)
    assert task.status == TaskStatus.SUCCESS
