"""把控制面登记的 artifact 部署到 service 所属 runtime（artifact 直接部署 Task 3）。

Canonical owner：ArtifactDeploymentService 拥有 artifact→runtime 的执行；
DeploymentService 管状态编排；runtime adapter 管命令翻译。

类型兼容映射：
  generic  → systemd only（tar 包需 SFTP 传输到目标机）
  docker   → docker / k8s（镜像 URI 直接拉取，无需传输）

systemd 执行顺序：
  1. SFTP 上传本地 tar 到 /tmp/axon-artifacts/<artifact_id>.tar.gz
  2. SystemdRuntime.deploy(DeploySpec) 解包并重启
  3. finally: rm -f 清理远端临时文件（失败只 warning，不覆盖部署结论）
"""

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

# 可注入的工厂类型：测试传 fake，生产用默认 build_* 函数。
TransferFactory = Callable[..., ArtifactTransfer]
ExecutorFactory = Callable[..., Executor]

# artifact 传至目标机的临时目录（固定前缀便于运维治理残留文件）。
_REMOTE_TMP_DIR = "/tmp/axon-artifacts"

# generic 制品兼容的 runtime 集合。
_GENERIC_RUNTIMES: frozenset[Runtime] = frozenset({Runtime.SYSTEMD})

# docker 制品兼容的 runtime 集合。
_DOCKER_RUNTIMES: frozenset[Runtime] = frozenset({Runtime.DOCKER, Runtime.K8S})


@dataclass(frozen=True)
class ArtifactDeployInput:
    """resolve() 返回的制品元数据快照，作为 deploy() 的执行依据。"""

    service_id: str
    artifact_id: str
    version: str | None
    git_sha: str | None
    uri: str
    registry_type: ArtifactRegistryType


class ArtifactDeploymentService:
    """把制品直接部署到目标 runtime。

    职责：artifact 归属校验、类型/runtime 兼容校验、placement 解析、
    执行（传输 + runtime 动作）。不管理 deployment 状态——状态编排由
    DeploymentService 负责。
    """

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
        agent_registry: AgentGatewayRegistry | None = None,
        k8s_api_factory: Callable[[], AppsV1ApiLike] | None = None,
        transfer_factory: TransferFactory | None = None,
        executor_factory: ExecutorFactory | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._connector = connector
        self._agent_registry = agent_registry
        self._k8s_api_factory = k8s_api_factory
        # 默认使用真实 build_* 工厂；测试可注入 fake 工厂屏蔽网络。
        self._transfer_factory: TransferFactory = (
            transfer_factory or build_artifact_transfer_for_server
        )
        self._executor_factory: ExecutorFactory = executor_factory or build_executor_for_server

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def resolve(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        """加载制品元数据，校验归属与 runtime 兼容性。

        成功返回 ArtifactDeployInput；失败抛 AppError。
        任何 runtime 动作发生前完成所有可静态验证。
        """
        async with self._db.session() as session:
            artifact_repo = ArtifactRepository(session)
            artifact = await artifact_repo.get_artifact(artifact_id)  # 404 if missing

            if artifact.service_id != service_id:
                raise AppError(
                    "artifact_service_mismatch",
                    "制品不属于目标服务，不能跨服务部署",
                    status_code=409,
                )

            registry = await artifact_repo.get_registry(artifact.registry_id)
            service = await ServiceRepository(session).get_service(service_id)
            _check_type_runtime_compat(registry.type, service.runtime)

            return ArtifactDeployInput(
                service_id=service_id,
                artifact_id=artifact.id,
                version=artifact.version,
                git_sha=artifact.git_sha,
                uri=artifact.uri,
                registry_type=registry.type,
            )

    async def deploy(self, service_id: str, artifact_id: str) -> ArtifactDeployInput:
        """把制品部署到该服务的全部 runtime placement。

        先 resolve（含校验），再按 runtime 类型执行部署。
        任一 placement 失败则整体上抛，不假装成功。
        """
        deploy_input = await self.resolve(service_id, artifact_id)

        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            placements = list(await ServiceRepository(session).list_placements(service_id))

            if not placements:
                raise AppError(
                    "no_placement",
                    "服务没有任何放置点，无法部署制品",
                    status_code=409,
                )

            server_repo = ServerRepository(session)
            servers: list[Server | None] = []
            for placement in placements:
                server = (
                    await server_repo.find(placement.server_id) if placement.server_id else None
                )
                servers.append(server)

            runtime = service.runtime
            runtime_ref = dict(service.runtime_ref or {})

        if runtime == Runtime.SYSTEMD:
            await self._deploy_systemd(
                deploy_input,
                _require_server(servers[0]),
                runtime_ref,
            )
        elif runtime == Runtime.DOCKER:
            await self._deploy_docker(deploy_input, servers, runtime_ref)
        elif runtime == Runtime.K8S:
            await self._deploy_k8s(deploy_input, runtime_ref)
        else:
            raise AppError(
                "artifact_runtime_mismatch",
                f"运行时 {runtime.value} 暂不支持 artifact 直接部署",
                status_code=409,
            )

        return deploy_input

    # ── runtime 分支 ──────────────────────────────────────────────────────

    async def _deploy_systemd(
        self,
        deploy_input: ArtifactDeployInput,
        server: Server,
        runtime_ref: dict[str, Any],
    ) -> None:
        """SFTP 上传 → SystemdRuntime.deploy → 远端临时文件清理。

        upload 失败直接上抛，不触发 runtime 动作。
        cleanup 失败只记 warning，不覆盖部署成功结论。
        """
        remote_path = f"{_REMOTE_TMP_DIR}/{deploy_input.artifact_id}.tar.gz"
        transfer = self._transfer_factory(
            server,
            self._secrets,
            connector=self._connector,
            agent_registry=self._agent_registry,
        )
        executor = self._build_executor(server)

        upload_ok = False
        try:
            await transfer.upload(deploy_input.uri, remote_path)
            upload_ok = True
            await SystemdRuntime(executor).deploy(
                DeploySpec(
                    artifact=remote_path,
                    unit_name=runtime_ref.get("unit_name"),
                    deploy_path=runtime_ref.get("deploy_path"),
                )
            )
        finally:
            if upload_ok:
                await self._cleanup_remote_tmp(executor, remote_path, deploy_input.artifact_id)

    async def _deploy_docker(
        self,
        deploy_input: ArtifactDeployInput,
        servers: list[Server | None],
        runtime_ref: dict[str, Any],
    ) -> None:
        """逐 placement 顺序部署 Docker 镜像；首个失败停止后续。"""
        env: dict[str, str] = {str(k): str(v) for k, v in (runtime_ref.get("env") or {}).items()}
        ports: list[str] = [str(p) for p in (runtime_ref.get("ports") or [])]
        spec = DeploySpec(
            artifact=deploy_input.uri,
            image=deploy_input.uri,
            container_name=runtime_ref.get("container_name"),
            env=env,
            ports=ports,
        )
        for server in servers:
            executor = self._build_executor(_require_server(server))
            await DockerRuntime(executor).deploy(spec)

    async def _deploy_k8s(
        self,
        deploy_input: ArtifactDeployInput,
        runtime_ref: dict[str, Any],
    ) -> None:
        """K8s：JSON Patch 替换首个容器镜像，由 Deployment 自身滚动完成更新。"""
        if self._k8s_api_factory is None:
            raise AppError(
                "runtime_not_configured",
                "未配置 k8s client，无法部署制品到 k8s runtime",
                status_code=501,
            )
        await K8sRuntime(self._k8s_api_factory()).deploy(
            DeploySpec(
                artifact=deploy_input.uri,
                image=deploy_input.uri,
                namespace=runtime_ref.get("namespace"),
                workload=runtime_ref.get("workload"),
            )
        )

    # ── helpers ───────────────────────────────────────────────────────────

    def _build_executor(self, server: Server) -> Executor:
        return self._executor_factory(
            server,
            self._secrets,
            connector=self._connector,
            agent_registry=self._agent_registry,
        )

    async def _cleanup_remote_tmp(
        self, executor: Executor, remote_path: str, artifact_id: str
    ) -> None:
        """尝试删除目标机上的临时制品文件；失败只记 warning，不抛。"""
        try:
            result = await executor.exec(f"rm -f {shlex.quote(remote_path)}")
            if not result.succeeded:
                log.warning(
                    "artifact_remote_cleanup_failed",
                    artifact_id=artifact_id,
                    exit_code=result.exit_code,
                    stderr=result.stderr[:200],
                )
        except Exception as exc:  # noqa: BLE001 — cleanup 失败不能影响部署结论
            log.warning(
                "artifact_remote_cleanup_error",
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
            )


# ── 模块级纯函数 ──────────────────────────────────────────────────────────


def _check_type_runtime_compat(registry_type: ArtifactRegistryType, runtime: Runtime) -> None:
    """校验制品类型与 runtime 是否兼容；不兼容抛 409。"""
    compatible = (
        registry_type == ArtifactRegistryType.GENERIC and runtime in _GENERIC_RUNTIMES
    ) or (registry_type == ArtifactRegistryType.DOCKER and runtime in _DOCKER_RUNTIMES)
    if not compatible:
        raise AppError(
            "artifact_runtime_mismatch",
            (
                f"{registry_type.value} 类型制品不能部署到 "
                f"{runtime.value} 运行时（兼容映射：generic→systemd，docker→docker/k8s）"
            ),
            status_code=409,
        )


def _require_server(server: Server | None) -> Server:
    """放置点必须关联具体服务器；否则抛 409。"""
    if server is None:
        raise AppError(
            "no_placement",
            "放置点未关联服务器，无法部署制品",
            status_code=409,
        )
    return server
