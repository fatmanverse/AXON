"""T1.10 / T3.6 生产接线验收:k8s 生命周期与发布策略经真实注入路径可用。

审计发现的根因:K8sRuntime 与 release_strategy 都完整,但生产从未构造 k8s client、
从未注入 rollout_provider——真实应用里 k8s 服务动作恒 501、策略层永不执行。本测试
覆盖生产接线本身:

- k8s_api_factory 经 deps.get_k8s_api_factory 从 app.state 取,注入 LifecycleService,
  k8s 服务的 restart 真的走 client patch(而非旧的 501 占位)。
- build_rollout_provider 为 k8s 服务组装出带 K8sRuntime 的 RolloutContext,为裸机服务
  组装出带 BareMetalTarget 的上下文;k8s 未启用时对 k8s 服务返回 None(诚实退回)。
"""

import pytest
import pytest_asyncio

from app.adapters.k8s_runtime import K8sRuntime
from app.core.config import Settings
from app.core.db import Database
from app.core.secrets import build_secret_store
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.lifecycle_service import LifecycleService
from app.services.rollout_provider import build_rollout_provider
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAppsV1Api:
    """记录调用的假 kubernetes AppsV1Api。"""

    def __init__(self) -> None:
        self.patched: list[dict] = []
        self.scaled: list[dict] = []

    async def patch_namespaced_deployment(self, name: str, namespace: str, body: dict):
        self.patched.append({"name": name, "namespace": namespace, "body": body})

    async def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: dict):
        self.scaled.append({"name": name, "namespace": namespace, "body": body})


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
def secrets():
    return build_secret_store(Settings(secret_backend="local", secret_master_key=""))


@pytest.fixture
def settings():
    return Settings(secret_backend="local", secret_master_key="", k8s_default_replicas=3)


async def _seed_k8s_service(db):
    async with db.session() as session:
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="gateway",
                env=ServiceEnvironment.STAGING,
                runtime=Runtime.K8S,
                runtime_ref={"namespace": "edge", "workload": "gateway"},
            )
        )
        await svc_repo.create_placement(PlacementCreate(service_id=service.id))
        return service.id


async def test_k8s_lifecycle_uses_injected_factory(db, secrets):
    """注入 k8s_api_factory 后,k8s 服务的 restart 经真实 client 执行并落 success。

    这是生产接线的关键断言:同一 factory 由 deps 从 app.state 取(见下一个测试对
    app.state 的覆写),注入后 k8s 服务不再命中 501 占位。"""
    service_id = await _seed_k8s_service(db)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    api = _FakeAppsV1Api()
    svc = LifecycleService(db, secrets, k8s_api_factory=lambda: api)
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    assert len(api.patched) == 1  # rollout restart 打了 annotation
    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.SUCCESS


async def test_rollout_provider_builds_k8s_context(db, secrets, settings):
    """rollout provider 为 k8s 服务组装出带 K8sRuntime 的上下文,replicas 取自 settings。"""
    service_id = await _seed_k8s_service(db)
    api = _FakeAppsV1Api()
    provider = build_rollout_provider(db, secrets, settings, k8s_api_factory=lambda: api)

    async with db.session() as session:
        service = await ServiceRepository(session).get_service(service_id)
    ctx = await provider(service)

    assert ctx is not None
    assert ctx.runtime == Runtime.K8S
    assert isinstance(ctx.k8s_adapter, K8sRuntime)
    assert ctx.namespace == "edge"
    assert ctx.workload == "gateway"
    assert ctx.replicas == 3


async def test_rollout_provider_k8s_none_when_disabled(db, secrets, settings):
    """k8s 未启用(factory=None)时,对 k8s 服务返回 None——诚实退回仅触发 CI,不假装。"""
    service_id = await _seed_k8s_service(db)
    provider = build_rollout_provider(db, secrets, settings, k8s_api_factory=None)
    async with db.session() as session:
        service = await ServiceRepository(session).get_service(service_id)
    assert await provider(service) is None


async def test_rollout_provider_builds_bare_context(db, secrets, settings):
    """裸机服务:为每个 placement 的 server 建 executor,组装出 BareMetalTarget。"""
    async with db.session() as session:
        server = await ServerRepository(session).create(
            ServerCreate(
                name="host-1",
                host="10.0.0.1",
                access_mode=AccessMode.SSH,
                ssh_credential_id="cred-1",
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.STAGING,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        await svc_repo.create_placement(PlacementCreate(service_id=service.id, server_id=server.id))
        service_id = service.id

    provider = build_rollout_provider(db, secrets, settings, k8s_api_factory=None)
    async with db.session() as session:
        service = await ServiceRepository(session).get_service(service_id)
    ctx = await provider(service)

    assert ctx is not None
    assert ctx.runtime == Runtime.SYSTEMD
    assert len(ctx.bare_targets) == 1
    assert ctx.bare_targets[0].ref == "billing.service"
