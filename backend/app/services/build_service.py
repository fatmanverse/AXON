"""本地构建编排(构建能力一期,方案 A「1 号构建节点」)。

对一条已落库的 build task,在控制面本地节点执行「clone → 测试 → build → 产出
制品」,并驱动 build 记录与 task 的状态机。与 AgentDeliveryService/DeploymentService
同构:分段提交(先标 running 让轮询可见,执行完另起会话落终态),全程不抛,结果
落在 build 与 task 状态上。

执行接缝经 executor_factory 注入(默认建 LocalExecutor,测试注入 fake),后续把
构建派到 SSH 构建节点时换工厂即可,本服务一行不改。产出制品经 ArtifactRepository
落库并回填 build.artifact_id,构成「代码 → 制品」可回溯链条(部署侧消费留二期)。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from app.adapters.build_runner import BuildOutcome, BuildRunner, BuildSpec
from app.adapters.executor import Executor
from app.adapters.local_executor import LocalExecutor
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
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._settings = settings
        self._executor_factory = executor_factory or (lambda workdir: LocalExecutor(workdir))

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
                await TaskRepository(session).mark_result(
                    task_id, TaskStatus.FAILED, error=message
                )
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
            # 一期只跑控制面本地节点;登记以在 build 上留可追溯的执行节点。
            node = await BuildNodeRepository(session).ensure_local_node()
            node_id = node.id
            # 构建启动时 git_ref 可能是分支名,构建记录里携带的 ref 优先(触发时可覆盖)。
            git_ref = self._resolve(build_config, "git_ref", default="main")

        workspace = Path(self._settings.build_workspace_dir) / build_id
        executor = self._executor_factory(workspace)
        try:
            spec = self._build_spec(
                build_config=build_config,
                service_name=service_name,
                build_id=build_id,
                git_ref=git_ref,
                workspace=workspace,
            )
            outcome = await BuildRunner(executor).run(spec)
            await self._persist_artifact(
                build_id=build_id,
                service_id=service_id,
                service_name=service_name,
                build_config=build_config,
                outcome=outcome,
                node_id=node_id,
            )
        finally:
            self._cleanup(workspace)

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
            log.warning("build_workspace_cleanup_failed", workspace=str(workspace),
                        error_type=type(exc).__name__)
