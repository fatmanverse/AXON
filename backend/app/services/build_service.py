"""构建编排：按能力和并发 lease 选择本地或 SSH 构建节点。

对一条已落库的 build task,在控制面本地节点执行「clone → 测试 → build → 产出
制品」,并驱动 build 记录与 task 的状态机。与 AgentDeliveryService/DeploymentService
同构:分段提交(先标 running 让轮询可见,执行完另起会话落终态),全程不抛,结果
落在 build 与 task 状态上。

执行接缝经 executor_factory 注入(默认本地 LocalExecutor,测试注入 fake)。SSH 节点
通过同一 Executor 接口执行，generic 制品经 SFTP 回传。产出制品经 ArtifactRepository
落库并回填 build.artifact_id,构成「代码 → 制品」可回溯链条(部署侧消费留二期)。
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.adapters.build_runner import BuildOutcome, BuildRunner, BuildSpec
from app.adapters.executor import Executor
from app.adapters.local_executor import LocalExecutor
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.artifact import ArtifactRegistryType
from app.models.build import BuildStatus
from app.models.task import TaskStatus
from app.services.artifact_repository import ArtifactRepository
from app.services.build_node_repository import BuildNodeRepository
from app.services.build_node_scheduler import BuildNodeScheduler
from app.services.build_repository import BuildRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

log = get_logger("build_service")

# 执行器工厂:入参 workdir,返回一个 Executor。默认建 LocalExecutor(本地子进程),
# 测试注入 fake 以隔离真实构建。
ExecutorFactory = Callable[[Path], Executor]


class BuildService:
    """编排本地构建:落 build/artifact、驱动 task,流转状态。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        settings: Settings,
        *,
        executor_factory: ExecutorFactory | None = None,
        redis=None,
        connector=None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._settings = settings
        self._executor_factory = executor_factory or (lambda workdir: LocalExecutor(workdir))
        self._connector = connector
        self._scheduler = BuildNodeScheduler(
            redis,
            lease_ttl_sec=settings.build_node_lease_ttl_sec,
        )

    async def run_build(self, *, task_id: str, build_id: str, service_id: str) -> None:
        """执行一次本地构建。全程不抛:结果落在 build 与 task 状态上。"""
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)
            await BuildRepository(session).mark_status(build_id, BuildStatus.RUNNING)

        try:
            await self._execute(build_id=build_id, service_id=service_id)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("build_failed", build_id=build_id, service_id=service_id, error=message)
            async with self._db.session() as session:
                await BuildRepository(session).mark_status(
                    build_id, BuildStatus.FAILED, error=message
                )
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        async with self._db.session() as session:
            await BuildRepository(session).mark_status(build_id, BuildStatus.SUCCESS)
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"action": "build"}
            )

    async def _execute(self, *, build_id: str, service_id: str) -> None:
        """载服务 build_config → 组 BuildSpec → 本地跑构建 → 落制品并回填 build。

        任一步失败即抛(由 run_build 落 build/task failed)。工作区无论成败都清理。
        """
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            build_config = service.build_config
            service_name = service.name
            if not build_config:
                raise AppError(
                    "build_not_configured",
                    "服务未配置构建(build_config 为空),无法本地构建",
                    status_code=400,
                )
            node_repo = BuildNodeRepository(session)
            await node_repo.ensure_local_node()
            nodes = list(await node_repo.list())
            required_labels = build_config.get("required_labels") or {}
            node, slot = await self._scheduler.acquire(
                nodes,
                required_labels=required_labels,
            )
            node_id = node.id
            remote_server = None
            if node.server_id:
                from app.services.server_repository import ServerRepository

                remote_server = await ServerRepository(session).get(node.server_id)
            # 构建启动时 git_ref 可能是分支名,构建记录里携带的 ref 优先(触发时可覆盖)。
            git_ref = self._resolve(build_config, "git_ref", default="main")

        workspace = Path(self._settings.build_workspace_dir) / build_id
        executor = self._build_executor(node, remote_server, workspace)
        try:
            spec = self._build_spec(
                build_config=build_config,
                service_name=service_name,
                build_id=build_id,
                git_ref=git_ref,
                workspace=workspace,
            )
            outcome = await BuildRunner(executor).run(spec)
            if not (node.labels or {}).get("local") and spec.artifact_type == "generic":
                local_artifact = Path(self._settings.build_artifacts_dir) / (
                    f"{service_name}-{build_id}.tar.gz"
                )
                download = getattr(executor, "download_artifact", None)
                if download is None:
                    raise AppError(
                        "build_artifact_fetch_not_supported",
                        "SSH 构建节点未提供制品回传能力",
                        status_code=501,
                    )
                await download(outcome.artifact_uri, str(local_artifact))
                outcome = replace(outcome, artifact_uri=str(local_artifact))
            await self._persist_artifact(
                build_id=build_id,
                service_id=service_id,
                service_name=service_name,
                build_config=build_config,
                outcome=outcome,
                node_id=node_id,
            )
        finally:
            if not (node.labels or {}).get("local"):
                try:
                    await executor.exec(f"rm -rf {shlex.quote(str(workspace))}")
                except Exception as exc:  # noqa: BLE001 - 构建结论不被清理覆盖
                    log.warning(
                        "remote_build_workspace_cleanup_failed", error_type=type(exc).__name__
                    )
            self._cleanup(workspace)
            await slot.release()

    def _build_executor(self, node, server, workspace: Path) -> Executor:
        if (node.labels or {}).get("local"):
            return self._executor_factory(workspace)
        if server is not None:
            from app.services.executor_factory import build_executor_for_server

            return build_executor_for_server(
                server,
                self._secrets,
                connector=self._connector,
            )
        if not node.host or not node.ssh_credential_id:
            raise AppError(
                "build_node_invalid",
                "外部构建节点必须提供 host 与 ssh_credential_id",
                status_code=400,
            )
        labels = node.labels or {}
        target = SSHTarget(
            host=node.host,
            port=int(labels.get("ssh_port", 22)),
            username=str(labels.get("ssh_username", "root")),
            credential_id=node.ssh_credential_id,
            auth_type=str(labels.get("ssh_auth_type", "key")),
        )
        return SSHExecutor(target, self._secrets, connector=self._connector)

    def _build_spec(
        self,
        *,
        build_config: dict,
        service_name: str,
        build_id: str,
        git_ref: str,
        workspace: Path,
    ) -> BuildSpec:
        artifact_type = str(build_config.get("artifact_type", "generic"))
        repo_url = self._resolve(build_config, "repo_url", required=True)
        build_command = self._resolve(build_config, "build_command", required=True)
        common = {
            "repo_url": repo_url,
            "git_ref": git_ref,
            "workspace": str(workspace),
            "build_command": build_command,
            "artifact_type": artifact_type,
            "test_command": build_config.get("test_command") or None,
        }
        if artifact_type == "docker":
            image_ref = self._resolve(build_config, "image_ref", required=True)
            return BuildSpec(
                **common,
                image_ref=image_ref,
                dockerfile=str(build_config.get("dockerfile", "Dockerfile")),
            )
        # generic:产物目录必填;tar 包落控制面本地制品目录,名字带 build_id 保唯一。
        output_path = self._resolve(build_config, "output_path", required=True)
        artifact_path = str(
            Path(self._settings.build_artifacts_dir) / f"{service_name}-{build_id}.tar.gz"
        )
        return BuildSpec(**common, output_path=output_path, artifact_path=artifact_path)

    async def _persist_artifact(
        self,
        *,
        build_id: str,
        service_id: str,
        service_name: str,
        build_config: dict,
        outcome: BuildOutcome,
        node_id: str,
    ) -> None:
        """落制品记录并回填 build.git_sha / build.artifact_id / build.build_node_id。"""
        artifact_type = str(build_config.get("artifact_type", "generic"))
        async with self._db.session() as session:
            artifact_repo = ArtifactRepository(session)
            registry_id = build_config.get("registry_id")
            if registry_id:
                registry = await artifact_repo.get_registry(registry_id)
            else:
                reg_type = (
                    ArtifactRegistryType.DOCKER
                    if artifact_type == "docker"
                    else ArtifactRegistryType.GENERIC
                )
                registry = await artifact_repo.ensure_default_registry(reg_type)
            artifact = await artifact_repo.create_artifact(
                registry_id=registry.id,
                service_id=service_id,
                build_id=build_id,
                git_sha=outcome.git_sha,
                name=service_name,
                version=build_config.get("version"),
                uri=outcome.artifact_uri,
                digest=outcome.digest,
                size_bytes=outcome.size_bytes,
            )
            build_repo = BuildRepository(session)
            build = await build_repo.get(build_id)
            build.git_sha = outcome.git_sha
            build.build_node_id = node_id
            build.artifact_id = artifact.id
            await session.flush()

    @staticmethod
    def _resolve(
        config: dict, key: str, *, default: str | None = None, required: bool = False
    ) -> str:
        value = config.get(key) or default
        if required and not value:
            raise AppError(
                "build_config_incomplete",
                f"构建配置缺少必填项: {key}",
                status_code=400,
            )
        return str(value) if value is not None else ""

    @staticmethod
    def _cleanup(workspace: Path) -> None:
        """清理构建工作区(用完即删,避免磁盘堆积)。删失败不致命,仅记日志。"""
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 —— 清理失败不得影响构建结论
            log.warning(
                "build_workspace_cleanup_failed",
                workspace=str(workspace),
                error_type=type(exc).__name__,
            )
