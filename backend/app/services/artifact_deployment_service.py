"""已登记 artifact 到 systemd、Docker、Kubernetes 的直接部署 owner。"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.agent_gateway_registry import AgentGatewayRegistry
from app.adapters.artifact_transfer import ArtifactTransfer
from app.adapters.docker_runtime import DockerRuntime
from app.adapters.executor import DeploySpec, Executor
from app.adapters.k8s_runtime import AppsV1ApiLike, K8sRuntime
from app.adapters.systemd_runtime import SystemdRuntime
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.artifact import ArtifactRegistryType
from app.models.server import Server
from app.models.service import Runtime
from app.services.artifact_repository import ArtifactRepository
from app.services.executor_factory import (
    build_artifact_transfer_for_server,
    build_executor_for_server,
)
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository

log = get_logger("artifact_deployment")

ExecutorFactory = Callable[..., Executor]
TransferFactory = Callable[..., ArtifactTransfer]


@dataclass(frozen=True)
class ArtifactDeployInput:
    service_id: str
    artifact_id: str
    version: str | None
    git_sha: str | None
    uri: str
    registry_type: ArtifactRegistryType


@dataclass(frozen=True)
class _ExecutionPlan:
    input: ArtifactDeployInput
    runtime: Runtime
    runtime_ref: dict[str, Any]
    servers: tuple[Server | None, ...]


class ArtifactDeploymentService:
    """校验 artifact/runtime 契约并调用现有 runtime adapters。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
        agent_registry: AgentGatewayRegistry | None = None,
        k8s_api_factory: Callable[[], AppsV1ApiLike] | None = None,
        executor_factory: ExecutorFactory = build_executor_for_server,
        transfer_factory: TransferFactory = build_artifact_transfer_for_server,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._connector = connector
        self._agent_registry = agent_registry
        self._k8s_api_factory = k8s_api_factory
        self._executor_factory = executor_factory
        self._transfer_factory = transfer_factory

    async def resolve(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        return (await self._build_plan(service_id, artifact_id)).input

    async def deploy(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        plan = await self._build_plan(service_id, artifact_id)
        if plan.runtime == Runtime.SYSTEMD:
            await self._deploy_systemd(plan)
        elif plan.runtime == Runtime.DOCKER:
            await self._deploy_docker(plan)
        elif plan.runtime == Runtime.K8S:
            await self._deploy_k8s(plan)
        else:  # _validate_compatibility 已阻止，保留显式不变量保护。
            raise AppError(
                "artifact_runtime_mismatch",
                f"制品类型不支持 {plan.runtime.value} 运行时",
                status_code=409,
            )
        return plan.input

    async def _build_plan(self, service_id: str, artifact_id: str) -> _ExecutionPlan:
        async with self._db.session() as session:
            artifact_repo = ArtifactRepository(session)
            artifact = await artifact_repo.get_artifact(artifact_id)
            if artifact.service_id != service_id:
                raise AppError(
                    "artifact_service_mismatch",
                    "制品不属于目标服务",
                    status_code=409,
                )

            service_repo = ServiceRepository(session)
            service = await service_repo.get_service(service_id)
            registry = await artifact_repo.get_registry(artifact.registry_id)
            self._validate_compatibility(service.runtime, registry.type)

            placements = list(await service_repo.list_placements(service_id))
            if not placements:
                raise AppError(
                    "no_placement",
                    "服务没有任何放置点,无法部署制品",
                    status_code=409,
                )

            server_repo = ServerRepository(session)
            servers: list[Server | None] = []
            for placement in placements:
                server = (
                    await server_repo.get(placement.server_id)
                    if placement.server_id is not None
                    else None
                )
                servers.append(server)

            runtime_ref = dict(service.runtime_ref or {})
            self._validate_runtime_ref(service.runtime, runtime_ref)
            deploy_input = ArtifactDeployInput(
                service_id=service_id,
                artifact_id=artifact.id,
                version=artifact.version,
                git_sha=artifact.git_sha,
                uri=artifact.uri,
                registry_type=registry.type,
            )
            return _ExecutionPlan(
                input=deploy_input,
                runtime=service.runtime,
                runtime_ref=runtime_ref,
                servers=tuple(servers),
            )

    @staticmethod
    def _validate_compatibility(
        runtime: Runtime,
        registry_type: ArtifactRegistryType,
    ) -> None:
        compatible = (
            registry_type == ArtifactRegistryType.GENERIC and runtime == Runtime.SYSTEMD
        ) or (
            registry_type == ArtifactRegistryType.DOCKER
            and runtime in {Runtime.DOCKER, Runtime.K8S}
        )
        if not compatible:
            raise AppError(
                "artifact_runtime_mismatch",
                f"{registry_type.value} 制品不支持 {runtime.value} 运行时",
                status_code=409,
            )

    @staticmethod
    def _validate_runtime_ref(runtime: Runtime, runtime_ref: dict[str, Any]) -> None:
        required: dict[Runtime, tuple[str, ...]] = {
            Runtime.SYSTEMD: ("unit_name", "deploy_path"),
            Runtime.DOCKER: ("container_name",),
            Runtime.K8S: ("namespace", "workload"),
        }
        missing = [key for key in required[runtime] if not runtime_ref.get(key)]
        if missing:
            raise AppError(
                "invalid_runtime_ref",
                f"{runtime.value} 服务的 runtime_ref 缺少 {', '.join(missing)}",
                status_code=400,
            )

    async def _deploy_systemd(self, plan: _ExecutionPlan) -> None:
        remote_path = f"/tmp/axon-artifacts/{plan.input.artifact_id}.tar.gz"
        spec = DeploySpec(
            artifact=remote_path,
            unit_name=str(plan.runtime_ref["unit_name"]),
            deploy_path=str(plan.runtime_ref["deploy_path"]),
        )
        for server in plan.servers:
            transfer = self._build_transfer(server)
            executor = self._build_executor(server)
            await transfer.upload(plan.input.uri, remote_path)
            try:
                await SystemdRuntime(executor).deploy(spec)
            finally:
                await self._cleanup_remote_artifact(executor, server, remote_path)

    async def _deploy_docker(self, plan: _ExecutionPlan) -> None:
        env = dict(plan.runtime_ref.get("env") or {})
        ports = list(plan.runtime_ref.get("ports") or [])
        spec = DeploySpec(
            artifact=plan.input.uri,
            image=plan.input.uri,
            container_name=str(plan.runtime_ref["container_name"]),
            env=env,
            ports=ports,
        )
        for server in plan.servers:
            await DockerRuntime(self._build_executor(server)).deploy(spec)

    async def _deploy_k8s(self, plan: _ExecutionPlan) -> None:
        if self._k8s_api_factory is None:
            raise AppError(
                "runtime_not_implemented",
                "未配置 k8s client,无法部署制品",
                status_code=501,
            )
        spec = DeploySpec(
            artifact=plan.input.uri,
            image=plan.input.uri,
            namespace=str(plan.runtime_ref["namespace"]),
            workload=str(plan.runtime_ref["workload"]),
        )
        await K8sRuntime(self._k8s_api_factory()).deploy(spec)

    def _build_executor(self, server: Server | None) -> Executor:
        return self._executor_factory(
            server,
            self._secrets,
            connector=self._connector,
            agent_registry=self._agent_registry,
        )

    def _build_transfer(self, server: Server | None) -> ArtifactTransfer:
        return self._transfer_factory(
            server,
            self._secrets,
            connector=self._connector,
        )

    async def _cleanup_remote_artifact(
        self,
        executor: Executor,
        server: Server | None,
        remote_path: str,
    ) -> None:
        try:
            result = await executor.exec(f"rm -f {shlex.quote(remote_path)}")
            if not result.succeeded:
                log.warning(
                    "artifact_cleanup_failed",
                    server_id=server.id if server is not None else None,
                    exit_code=result.exit_code,
                )
        except Exception as exc:
            log.warning(
                "artifact_cleanup_failed",
                server_id=server.id if server is not None else None,
                error_type=type(exc).__name__,
            )
