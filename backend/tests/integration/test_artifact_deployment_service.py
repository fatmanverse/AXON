"""ArtifactDeploymentService 的类型校验、目标解析与 runtime 执行。"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.db import Database
from app.core.errors import AppError
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.artifact import ArtifactRegistryType
from app.models.base import Base
from app.models.server import AccessMode, Server
from app.models.service import Runtime, Service, ServicePlacement
from app.services.artifact_deployment_service import ArtifactDeploymentService
from app.services.artifact_repository import ArtifactRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
def secrets():
    return LocalSecretStore(master_key=generate_master_key())


class RecordingExecutor(Executor):
    def __init__(
        self,
        target: str,
        events: list[str],
        *,
        fail_on: str | None = None,
    ) -> None:
        self._target = target
        self._events = events
        self._fail_on = fail_on

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self._events.append(f"exec:{self._target}:{command}")
        failed = self._fail_on is not None and self._fail_on in command
        return CommandResult(
            exit_code=1 if failed else 0,
            stdout="",
            stderr="boom" if failed else "",
        )

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


class RecordingTransfer:
    def __init__(
        self,
        target: str,
        events: list[str],
        *,
        fail: bool = False,
    ) -> None:
        self._target = target
        self._events = events
        self._fail = fail

    async def upload(self, local_path: str, remote_path: str) -> None:
        self._events.append(f"upload:{self._target}:{local_path}:{remote_path}")
        if self._fail:
            raise AppError("artifact_upload_failed", "upload failed", status_code=502)


class RecordingK8sApi:
    def __init__(self) -> None:
        self.patches: list[dict[str, Any]] = []

    async def patch_namespaced_deployment(
        self,
        name: str,
        namespace: str,
        body: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        self.patches.append({"name": name, "namespace": namespace, "body": body, "kwargs": kwargs})


async def _seed(
    db: Database,
    *,
    runtime: Runtime,
    registry_type: ArtifactRegistryType,
    runtime_ref: dict[str, Any] | None = None,
    placement_ids: tuple[str, ...] = ("a" * 32,),
    artifact_service_id: str | None = None,
) -> tuple[str, str, dict[str, Server]]:
    async with db.session() as session:
        service = Service(
            name=f"svc-{runtime.value}-{registry_type.value}-{len(placement_ids)}",
            env="prod",
            runtime=runtime,
            runtime_ref=runtime_ref or {},
        )
        session.add(service)
        await session.flush()

        servers: dict[str, Server] = {}
        for index, server_id in enumerate(placement_ids):
            server = Server(
                id=server_id,
                name=f"server-{server_id[0]}-{index}",
                host=f"10.0.0.{index + 10}",
                access_mode=AccessMode.SSH,
                ssh_credential_id=f"credential-{index}",
                labels={},
            )
            session.add(server)
            servers[server_id] = server
        await session.flush()

        if runtime == Runtime.K8S:
            for _ in placement_ids:
                session.add(ServicePlacement(service_id=service.id, server_id=None))
        else:
            for server_id in placement_ids:
                session.add(ServicePlacement(service_id=service.id, server_id=server_id))

        artifact_repo = ArtifactRepository(session)
        registry = await artifact_repo.create_registry(
            name=f"registry-{service.id}",
            type_=registry_type,
        )
        artifact = await artifact_repo.create_artifact(
            registry_id=registry.id,
            service_id=artifact_service_id or service.id,
            name="billing",
            uri=(
                "/var/lib/axon/artifacts/billing.tar.gz"
                if registry_type == ArtifactRegistryType.GENERIC
                else "registry.example.com/team/billing:1.2.3"
            ),
            version="1.2.3",
            git_sha="abc123",
        )
        return service.id, artifact.id, servers


def _service(
    db: Database,
    secrets: LocalSecretStore,
    *,
    events: list[str] | None = None,
    upload_fail_on: str | None = None,
    exec_failures: dict[str, str] | None = None,
    k8s_api: RecordingK8sApi | None = None,
) -> ArtifactDeploymentService:
    recorded = events if events is not None else []
    failures = exec_failures or {}

    def executor_factory(server: Server | None, *_args: Any, **_kwargs: Any) -> Executor:
        assert server is not None
        return RecordingExecutor(
            server.id,
            recorded,
            fail_on=failures.get(server.id),
        )

    def transfer_factory(server: Server | None, *_args: Any, **_kwargs: Any):
        assert server is not None
        return RecordingTransfer(
            server.id,
            recorded,
            fail=server.id == upload_fail_on,
        )

    return ArtifactDeploymentService(
        db,
        secrets,
        executor_factory=executor_factory,
        transfer_factory=transfer_factory,
        k8s_api_factory=(lambda: k8s_api) if k8s_api is not None else None,
    )


async def test_resolve_returns_artifact_source_of_truth(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.DOCKER,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={"container_name": "billing"},
    )

    resolved = await _service(db, secrets).resolve(service_id, artifact_id)

    assert resolved.service_id == service_id
    assert resolved.artifact_id == artifact_id
    assert resolved.version == "1.2.3"
    assert resolved.git_sha == "abc123"
    assert resolved.uri == "registry.example.com/team/billing:1.2.3"
    assert resolved.registry_type == ArtifactRegistryType.DOCKER


async def test_resolve_missing_artifact_raises_404(db, secrets):
    with pytest.raises(AppError) as caught:
        await _service(db, secrets).resolve("s" * 32, "a" * 32)

    assert caught.value.code == "artifact_not_found"
    assert caught.value.status_code == 404


async def test_resolve_rejects_cross_service_artifact(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.DOCKER,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={"container_name": "billing"},
        artifact_service_id="x" * 32,
    )

    with pytest.raises(AppError) as caught:
        await _service(db, secrets).resolve(service_id, artifact_id)

    assert caught.value.code == "artifact_service_mismatch"
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    ("runtime", "registry_type"),
    [
        (Runtime.SYSTEMD, ArtifactRegistryType.DOCKER),
        (Runtime.DOCKER, ArtifactRegistryType.GENERIC),
        (Runtime.K8S, ArtifactRegistryType.GENERIC),
    ],
)
async def test_resolve_rejects_artifact_runtime_mismatch(
    db,
    secrets,
    runtime: Runtime,
    registry_type: ArtifactRegistryType,
):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=runtime,
        registry_type=registry_type,
        runtime_ref={"unused": "value"},
    )

    with pytest.raises(AppError) as caught:
        await _service(db, secrets).resolve(service_id, artifact_id)

    assert caught.value.code == "artifact_runtime_mismatch"
    assert caught.value.status_code == 409


async def test_systemd_uploads_deploys_and_cleans_up(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.SYSTEMD,
        registry_type=ArtifactRegistryType.GENERIC,
        runtime_ref={"unit_name": "billing.service", "deploy_path": "/opt/billing"},
    )
    events: list[str] = []

    result = await _service(db, secrets, events=events).deploy(service_id, artifact_id)

    remote_path = f"/tmp/axon-artifacts/{artifact_id}.tar.gz"
    assert result.artifact_id == artifact_id
    assert events[0] == (f"upload:{'a' * 32}:/var/lib/axon/artifacts/billing.tar.gz:{remote_path}")
    assert "tar xzf" in events[1]
    assert remote_path in events[1]
    assert "systemctl daemon-reload" in events[2]
    assert "systemctl restart billing.service" in events[3]
    assert events[4].endswith(f"rm -f {remote_path}")


async def test_systemd_upload_failure_runs_no_runtime_action(db, secrets):
    server_id = "a" * 32
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.SYSTEMD,
        registry_type=ArtifactRegistryType.GENERIC,
        runtime_ref={"unit_name": "billing.service", "deploy_path": "/opt/billing"},
        placement_ids=(server_id,),
    )
    events: list[str] = []

    with pytest.raises(AppError) as caught:
        await _service(
            db,
            secrets,
            events=events,
            upload_fail_on=server_id,
        ).deploy(service_id, artifact_id)

    assert caught.value.code == "artifact_upload_failed"
    assert events == [
        f"upload:{server_id}:/var/lib/axon/artifacts/billing.tar.gz:"
        f"/tmp/axon-artifacts/{artifact_id}.tar.gz"
    ]


async def test_systemd_deploy_failure_still_cleans_up(db, secrets):
    server_id = "a" * 32
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.SYSTEMD,
        registry_type=ArtifactRegistryType.GENERIC,
        runtime_ref={"unit_name": "billing.service", "deploy_path": "/opt/billing"},
        placement_ids=(server_id,),
    )
    events: list[str] = []

    with pytest.raises(AppError) as caught:
        await _service(
            db,
            secrets,
            events=events,
            exec_failures={server_id: "tar xzf"},
        ).deploy(service_id, artifact_id)

    assert caught.value.code == "systemd_action_failed"
    assert events[-1].endswith(f"rm -f /tmp/axon-artifacts/{artifact_id}.tar.gz")


async def test_docker_deploys_placements_in_order(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.DOCKER,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={
            "container_name": "billing",
            "env": {"ENV": "prod"},
            "ports": ["8080:80"],
        },
        placement_ids=("b" * 32, "a" * 32),
    )
    events: list[str] = []

    await _service(db, secrets, events=events).deploy(service_id, artifact_id)

    pulls = [event for event in events if "docker pull" in event]
    assert pulls == [
        f"exec:{'a' * 32}:docker pull registry.example.com/team/billing:1.2.3",
        f"exec:{'b' * 32}:docker pull registry.example.com/team/billing:1.2.3",
    ]
    assert any("-e ENV=prod" in event for event in events)
    assert any("-p 8080:80" in event for event in events)


async def test_docker_stops_after_first_placement_failure(db, secrets):
    first = "a" * 32
    second = "b" * 32
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.DOCKER,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={"container_name": "billing"},
        placement_ids=(second, first),
    )
    events: list[str] = []

    with pytest.raises(AppError) as caught:
        await _service(
            db,
            secrets,
            events=events,
            exec_failures={first: "docker pull"},
        ).deploy(service_id, artifact_id)

    assert caught.value.code == "docker_action_failed"
    assert all(second not in event for event in events)


async def test_k8s_patches_artifact_image_once(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.K8S,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={"namespace": "billing-prod", "workload": "billing"},
    )
    api = RecordingK8sApi()

    await _service(db, secrets, k8s_api=api).deploy(service_id, artifact_id)

    assert len(api.patches) == 1
    assert api.patches[0]["name"] == "billing"
    assert api.patches[0]["namespace"] == "billing-prod"
    assert api.patches[0]["body"][0]["value"] == ("registry.example.com/team/billing:1.2.3")


async def test_deploy_rejects_missing_runtime_ref_before_remote_action(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.SYSTEMD,
        registry_type=ArtifactRegistryType.GENERIC,
        runtime_ref={"unit_name": "billing.service"},
    )
    events: list[str] = []

    with pytest.raises(AppError) as caught:
        await _service(db, secrets, events=events).deploy(service_id, artifact_id)

    assert caught.value.code == "invalid_runtime_ref"
    assert events == []


async def test_deploy_rejects_service_without_placements(db, secrets):
    service_id, artifact_id, _ = await _seed(
        db,
        runtime=Runtime.DOCKER,
        registry_type=ArtifactRegistryType.DOCKER,
        runtime_ref={"container_name": "billing"},
        placement_ids=(),
    )

    with pytest.raises(AppError) as caught:
        await _service(db, secrets).deploy(service_id, artifact_id)

    assert caught.value.code == "no_placement"
    assert caught.value.status_code == 409
