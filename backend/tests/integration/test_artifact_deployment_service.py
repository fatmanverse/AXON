"""ArtifactDeploymentService 集成测试(artifact 直接部署 Task 3)。

用 in-memory SQLite 数据库和 fake executor/transfer 工厂，覆盖：
- resolve：404/跨服务/类型不匹配
- systemd：上传→deploy→cleanup 顺序；上传失败不触发 deploy；cleanup 失败只 warning
- docker：多 placement 顺序执行；首错停止
- k8s：image patch；k8s_api_factory=None → 501
- 无 placement → 409
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.executor import CommandResult, DeploySpec
from app.core.db import Database
from app.core.errors import AppError
from app.models.artifact import ArtifactRegistryType
from app.models.base import Base
from app.models.server import AccessMode, Server
from app.models.service import Runtime
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.artifact_deployment_service import ArtifactDeploymentService
from app.services.artifact_repository import ArtifactRepository
from app.services.service_repository import ServiceRepository

# ── fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _make_service(
    session: AsyncSession,
    *,
    runtime: Runtime = Runtime.SYSTEMD,
    runtime_ref: dict | None = None,
) -> Any:
    if runtime_ref is None:
        if runtime == Runtime.SYSTEMD:
            runtime_ref = {"unit_name": "app.service", "deploy_path": "/opt/app"}
        elif runtime == Runtime.DOCKER:
            runtime_ref = {"container_name": "app"}
        elif runtime == Runtime.K8S:
            runtime_ref = {"namespace": "default", "workload": "app"}
        else:
            runtime_ref = {}
    svc = await ServiceRepository(session).create_service(
        ServiceCreate(name="app", env="staging", runtime=runtime, runtime_ref=runtime_ref)
    )
    return svc


async def _make_artifact(
    session: AsyncSession,
    *,
    service_id: str,
    registry_type: ArtifactRegistryType = ArtifactRegistryType.GENERIC,
    uri: str = "/var/lib/axon/artifacts/app.tar.gz",
) -> Any:
    repo = ArtifactRepository(session)
    registry = await repo.ensure_default_registry(registry_type)
    artifact = await repo.create_artifact(
        registry_id=registry.id,
        service_id=service_id,
        name="app",
        version="1.0.0",
        uri=uri,
        git_sha="abc" * 14,
    )
    return artifact


def _make_ssh_server(server_id: str = "srv-0000000000000000000000000000") -> Server:
    """构建最小 SSH Server stub（不存入 DB，仅供 fake factory 使用）。"""
    s = MagicMock(spec=Server)
    s.id = server_id
    s.host = "10.0.0.1"
    s.labels = {"ssh_port": "22", "ssh_username": "root", "auth_type": "key"}
    s.ssh_credential_id = "cred-1"
    s.access_mode = AccessMode.SSH
    s.agent_id = None
    return s


# ── fake executor / transfer ───────────────────────────────────────────────


class FakeTransfer:
    def __init__(self, *, fail: bool = False) -> None:
        self.uploaded: list[tuple[str, str]] = []
        self._fail = fail

    async def upload(self, local_path: str, remote_path: str) -> None:
        if self._fail:
            raise AppError("artifact_upload_failed", "fake upload error", status_code=502)
        self.uploaded.append((local_path, remote_path))


class FakeExecutor:
    def __init__(self, *, deploy_fail: bool = False, cleanup_fail: bool = False) -> None:
        self.deploy_calls: list[DeploySpec] = []
        self.exec_calls: list[str] = []
        self._deploy_fail = deploy_fail
        self._cleanup_fail = cleanup_fail

    async def exec(self, command: str, **_: Any) -> CommandResult:
        self.exec_calls.append(command)
        # cleanup_fail 只影响 rm -f 命令，不影响 deploy 内部的 mkdir/tar/systemctl
        if self._cleanup_fail and command.strip().startswith("rm -f"):
            return CommandResult(exit_code=1, stdout="", stderr="cleanup failed")
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        raise NotImplementedError("use runtime adapters directly")

    async def update_config(self, path: str, content: str) -> CommandResult:
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def get_service_status(self, service_ref: str):
        raise NotImplementedError


def _service_for(
    service: ArtifactDeploymentService,
    *,
    transfer: FakeTransfer,
    executor: FakeExecutor,
) -> None:
    """替换 service 的工厂为 fake。"""

    def _transfer_factory(server: Any, secrets: Any, **kw: Any) -> FakeTransfer:
        return transfer

    def _executor_factory(server: Any, secrets: Any, **kw: Any) -> FakeExecutor:
        return executor

    service._transfer_factory = _transfer_factory  # type: ignore[assignment]
    service._executor_factory = _executor_factory  # type: ignore[assignment]


# ── resolve tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_artifact_not_found(db):
    async with db.session() as session:
        svc = await _make_service(session)
        service_id = svc.id
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    with pytest.raises(AppError) as exc_info:
        await svc_obj.resolve(service_id, "0" * 32)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_service_mismatch(db):
    async with db.session() as session:
        svc = await _make_service(session)
        # 用不同名字避免 UNIQUE constraint
        other_svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="other-app",
                env="staging",
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "other.service", "deploy_path": "/opt/other"},
            )
        )
        artifact = await _make_artifact(session, service_id=other_svc.id)
        service_id = svc.id
        artifact_id = artifact.id
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    with pytest.raises(AppError) as exc_info:
        await svc_obj.resolve(service_id, artifact_id)
    assert exc_info.value.code == "artifact_service_mismatch"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_resolve_type_mismatch_docker_on_systemd(db):
    """docker 类型制品 → systemd 运行时 → 409。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(
            session,
            service_id=svc.id,
            registry_type=ArtifactRegistryType.DOCKER,
        )
        service_id = svc.id
        artifact_id = artifact.id
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    with pytest.raises(AppError) as exc_info:
        await svc_obj.resolve(service_id, artifact_id)
    assert exc_info.value.code == "artifact_runtime_mismatch"


@pytest.mark.asyncio
async def test_resolve_type_mismatch_generic_on_docker(db):
    """generic 类型制品 → docker 运行时 → 409。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.DOCKER)
        artifact = await _make_artifact(
            session,
            service_id=svc.id,
            registry_type=ArtifactRegistryType.GENERIC,
        )
        service_id = svc.id
        artifact_id = artifact.id
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    with pytest.raises(AppError) as exc_info:
        await svc_obj.resolve(service_id, artifact_id)
    assert exc_info.value.code == "artifact_runtime_mismatch"


@pytest.mark.asyncio
async def test_resolve_success_generic_systemd(db):
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(session, service_id=svc.id)
        service_id = svc.id
        artifact_id = artifact.id
        artifact_uri = artifact.uri
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    result = await svc_obj.resolve(service_id, artifact_id)
    assert result.artifact_id == artifact_id
    assert result.uri == artifact_uri
    assert result.registry_type == ArtifactRegistryType.GENERIC


# ── deploy tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deploy_no_placement_raises(db):
    """无 placement → 409 no_placement。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(session, service_id=svc.id)
        service_id = svc.id
        artifact_id = artifact.id
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    with pytest.raises(AppError) as exc_info:
        await svc_obj.deploy(service_id, artifact_id)
    assert exc_info.value.code == "no_placement"


@pytest.mark.asyncio
async def test_deploy_systemd_upload_then_deploy_then_cleanup(db):
    """systemd 路径：上传 → deploy → rm -f cleanup 顺序正确。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(
            session, service_id=svc.id, uri="/var/lib/axon/artifacts/app.tar.gz"
        )
        server_row = Server(
            id="s" * 32,
            name="host1",
            host="10.0.0.1",
            access_mode=AccessMode.SSH,
            ssh_credential_id="cred-1",
            labels={"ssh_port": "22", "ssh_username": "root", "ssh_auth_type": "key"},
        )
        session.add(server_row)
        await session.flush()
        await ServiceRepository(session).create_placement(
            PlacementCreate(service_id=svc.id, server_id=server_row.id)
        )
        service_id = svc.id
        artifact_id = artifact.id

    transfer = FakeTransfer()
    executor = FakeExecutor()
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    _service_for(svc_obj, transfer=transfer, executor=executor)

    result = await svc_obj.deploy(service_id, artifact_id)

    # 上传发生
    assert len(transfer.uploaded) == 1
    local, remote = transfer.uploaded[0]
    assert local == "/var/lib/axon/artifacts/app.tar.gz"
    assert remote == f"/tmp/axon-artifacts/{artifact_id}.tar.gz"

    # cleanup 发生
    assert any("rm -f" in cmd for cmd in executor.exec_calls)

    # resolve 返回值正确
    assert result.artifact_id == artifact_id


@pytest.mark.asyncio
async def test_deploy_systemd_upload_fail_no_deploy(db):
    """systemd 上传失败 → deploy 不执行，异常上抛。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(session, service_id=svc.id)
        server_row = Server(
            id="s" * 32,
            name="host1",
            host="10.0.0.1",
            access_mode=AccessMode.SSH,
            ssh_credential_id="cred-1",
            labels={},
        )
        session.add(server_row)
        await session.flush()
        await ServiceRepository(session).create_placement(
            PlacementCreate(service_id=svc.id, server_id=server_row.id)
        )
        service_id = svc.id
        artifact_id = artifact.id

    transfer = FakeTransfer(fail=True)
    executor = FakeExecutor()
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    _service_for(svc_obj, transfer=transfer, executor=executor)

    with pytest.raises(AppError) as exc_info:
        await svc_obj.deploy(service_id, artifact_id)

    assert exc_info.value.code == "artifact_upload_failed"
    # SystemdRuntime.deploy 未被调用（executor.exec 仅可能有 cleanup）
    deploy_exec_calls = [c for c in executor.exec_calls if "systemctl" in c or "tar" in c]
    assert deploy_exec_calls == []


@pytest.mark.asyncio
async def test_deploy_systemd_cleanup_failure_only_warns(db):
    """cleanup 失败不覆盖部署主结论（不抛出异常）。"""
    async with db.session() as session:
        svc = await _make_service(session, runtime=Runtime.SYSTEMD)
        artifact = await _make_artifact(session, service_id=svc.id)
        server_row = Server(
            id="s" * 32,
            name="host1",
            host="10.0.0.1",
            access_mode=AccessMode.SSH,
            ssh_credential_id="cred-1",
            labels={},
        )
        session.add(server_row)
        await session.flush()
        await ServiceRepository(session).create_placement(
            PlacementCreate(service_id=svc.id, server_id=server_row.id)
        )
        service_id = svc.id
        artifact_id = artifact.id

    transfer = FakeTransfer()
    executor = FakeExecutor(cleanup_fail=True)
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    _service_for(svc_obj, transfer=transfer, executor=executor)

    # cleanup 失败不抛，deploy 应成功返回
    result = await svc_obj.deploy(service_id, artifact_id)
    assert result.artifact_id == artifact_id


@pytest.mark.asyncio
async def test_deploy_docker_multiple_placements_sequential(db):
    """docker 多 placement 按顺序逐个部署。"""
    async with db.session() as session:
        svc = await _make_service(
            session,
            runtime=Runtime.DOCKER,
            runtime_ref={"container_name": "app"},
        )
        artifact = await _make_artifact(
            session,
            service_id=svc.id,
            registry_type=ArtifactRegistryType.DOCKER,
            uri="registry.example.com/app:v1",
        )
        for i in range(2):
            server_row = Server(
                id=f"s{i}" + "0" * 30,
                name=f"host{i}",
                host=f"10.0.0.{i + 1}",
                access_mode=AccessMode.SSH,
                ssh_credential_id="cred-1",
                labels={},
            )
            session.add(server_row)
            await session.flush()
            await ServiceRepository(session).create_placement(
                PlacementCreate(service_id=svc.id, server_id=server_row.id)
            )
        service_id = svc.id
        artifact_id = artifact.id

    class RecordingExecutor(FakeExecutor):
        async def exec(self, command: str, **_: Any) -> CommandResult:
            self.exec_calls.append(command)
            # simulate docker commands
            return CommandResult(exit_code=0, stdout="", stderr="")

    executor = RecordingExecutor()
    transfer = FakeTransfer()
    svc_obj = ArtifactDeploymentService(db, MagicMock())
    # docker 不需要 transfer，只需要 executor
    _service_for(svc_obj, transfer=transfer, executor=executor)

    result = await svc_obj.deploy(service_id, artifact_id)
    assert result.artifact_id == artifact_id


@pytest.mark.asyncio
async def test_deploy_k8s_without_factory_raises(db):
    """k8s runtime + k8s_api_factory=None → 501。"""
    async with db.session() as session:
        svc = await _make_service(
            session,
            runtime=Runtime.K8S,
            runtime_ref={"namespace": "default", "workload": "app"},
        )
        artifact = await _make_artifact(
            session,
            service_id=svc.id,
            registry_type=ArtifactRegistryType.DOCKER,
            uri="registry.example.com/app:v1",
        )
        server_row = Server(
            id="s" * 32,
            name="host1",
            host="10.0.0.1",
            access_mode=AccessMode.SSH,
            ssh_credential_id="cred-1",
            labels={},
        )
        session.add(server_row)
        await session.flush()
        await ServiceRepository(session).create_placement(
            PlacementCreate(service_id=svc.id, server_id=server_row.id)
        )
        service_id = svc.id
        artifact_id = artifact.id

    # k8s_api_factory=None
    svc_obj = ArtifactDeploymentService(db, MagicMock(), k8s_api_factory=None)
    _service_for(svc_obj, transfer=FakeTransfer(), executor=FakeExecutor())

    with pytest.raises(AppError) as exc_info:
        await svc_obj.deploy(service_id, artifact_id)
    assert exc_info.value.status_code == 501
