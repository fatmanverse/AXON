"""T1.10 服务生命周期执行核心(LifecycleService)。

验证纯 async 的执行核心:按 service.runtime 路由到运行时适配、对每个
placement 执行动作、task 状态正确流转(running→success/failed)。用内存
sqlite + fake connector,不触真实 SSH。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.lifecycle_service import LifecycleService
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


class _FakeProcess:
    def __init__(self, exit_status: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    """记录所有跑过的命令;可配置为失败。"""

    ran: list[str] = []

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok

    async def run(self, command: str, *, timeout: float | None = None) -> _FakeProcess:
        _FakeConnection.ran.append(command)
        if self._ok:
            return _FakeProcess(exit_status=0, stdout="ok", stderr="")
        return _FakeProcess(exit_status=1, stdout="", stderr="boom")

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest_asyncio.fixture
def secrets():
    return LocalSecretStore(master_key=generate_master_key())


async def _seed_systemd_service(db, secrets, *, runtime=Runtime.SYSTEMD):
    """建一台 SSH 服务器 + 一个 systemd 服务 + 一个放置,返回 (service_id,)。"""
    async with db.session() as session:
        cred_id = secrets.put("ssh-key", _FAKE_KEY)
        server = await ServerRepository(session).create(
            ServerCreate(
                name="host-01",
                host="10.0.0.9",
                access_mode=AccessMode.SSH,
                ssh_credential_id=cred_id,
                labels={"ssh_username": "ops", "ssh_port": 22},
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=runtime,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        await svc_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=server.id)
        )
        return service.id


@pytest.fixture(autouse=True)
def _reset_calls():
    _FakeConnection.ran = []
    yield
    _FakeConnection.ran = []


async def test_restart_runs_systemctl_and_marks_success(db, secrets):
    service_id = await _seed_systemd_service(db, secrets)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, connector=lambda **_: _FakeConnection(ok=True))
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    assert any("systemctl restart billing.service" in c for c in _FakeConnection.ran)
    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.SUCCESS


async def test_failed_action_marks_task_failed(db, secrets):
    service_id = await _seed_systemd_service(db, secrets)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.STOP, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, connector=lambda **_: _FakeConnection(ok=False))
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.STOP)

    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.FAILED
        assert refreshed.error


async def test_start_stop_map_to_correct_commands(db, secrets):
    service_id = await _seed_systemd_service(db, secrets)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.START, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, connector=lambda **_: _FakeConnection(ok=True))
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.START)

    assert any("systemctl start billing.service" in c for c in _FakeConnection.ran)


async def test_agent_mode_service_marks_task_failed_not_connected(db, secrets):
    """access_mode=agent 的服务器执行动作应落 failed(Agent 未接入),不影响 task 机制。"""
    async with db.session() as session:
        server = await ServerRepository(session).create(
            ServerCreate(
                name="agent-host",
                host="10.0.0.30",
                access_mode=AccessMode.AGENT,
                agent_id="agent-1",
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="reporting",
                env=ServiceEnvironment.DEV,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "reporting.service"},
            )
        )
        await svc_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=server.id)
        )
        service_id = service.id
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, connector=lambda **_: _FakeConnection(ok=True))
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.FAILED


async def test_docker_restart_runs_docker_command_and_marks_success(db, secrets):
    """docker runtime 的 restart 应经 SSH 下发 docker restart 并落 success。"""
    async with db.session() as session:
        cred_id = secrets.put("ssh-key", _FAKE_KEY)
        server = await ServerRepository(session).create(
            ServerCreate(
                name="docker-host",
                host="10.0.0.20",
                access_mode=AccessMode.SSH,
                ssh_credential_id=cred_id,
                labels={"ssh_username": "ops", "ssh_port": 22},
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="cache",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.DOCKER,
                runtime_ref={"container_name": "cache"},
            )
        )
        await svc_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=server.id)
        )
        service_id = service.id
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, connector=lambda **_: _FakeConnection(ok=True))
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    assert any("docker restart cache" in c for c in _FakeConnection.ran)
    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.SUCCESS


class _FakeAppsV1Api:
    """记录调用的假 kubernetes AppsV1Api(仅本集成测试用到的方法)。"""

    def __init__(self) -> None:
        self.patched: list[dict] = []

    async def patch_namespaced_deployment(self, name: str, namespace: str, body: dict):
        self.patched.append({"name": name, "namespace": namespace, "body": body})


async def _seed_k8s_service(db):
    """建一个无 server 的 k8s 服务 + 无 server_id 的放置,返回 service_id。"""
    async with db.session() as session:
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="gateway",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.K8S,
                runtime_ref={"namespace": "edge", "workload": "gateway"},
            )
        )
        await svc_repo.create_placement(PlacementCreate(service_id=service.id))
        return service.id


async def test_k8s_restart_routes_to_client_and_marks_success(db, secrets):
    """k8s runtime 的 restart 应经注入的 client patch Deployment 并落 success。"""
    service_id = await _seed_k8s_service(db)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    api = _FakeAppsV1Api()
    svc = LifecycleService(db, secrets, k8s_api_factory=lambda: api)
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    assert len(api.patched) == 1
    assert api.patched[0]["name"] == "gateway"
    assert api.patched[0]["namespace"] == "edge"
    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.SUCCESS


async def test_k8s_without_client_marks_task_failed(db, secrets):
    """未配置 k8s client 时,对 k8s 服务的动作应明确落 failed,不静默。"""
    service_id = await _seed_k8s_service(db)
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets)  # 未注入 k8s_api_factory
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.FAILED
        assert refreshed.error
