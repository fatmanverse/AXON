"""UI 触发部署的编排核心(T2.3,设计 §8.1 模式 A)。

纯 async 编排:接收一个已落库的 deploy task,加载服务→落一条 deployment
(running, source=ui-triggered)→调 PipelineAdapter 触发 CI→据触发结果流转
deployment 与 task 状态。与传输层解耦,可被 FastAPI BackgroundTasks 直接 await。

设计要点:
- adapter_provider 按 service 返回对应 PipelineAdapter(生产按服务配置选
  Jenkins/GitLab,测试注入 fake),本服务不关心 pipeline 选型细节。
- deployment 与 task 双记录:deployment 是业务「焊点」(§14.3),task 是异步执行
  载体(供前端轮询)。两者状态同步流转。
- previous_deployment_id 挂到上一次成功部署,支撑历史版本回滚链路(§11.2)。
- 全程不抛:失败落在 deployment.failed + task.failed,错误摘要入 task。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.pipeline import PipelineAdapter
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.deployment import (
    Deployment,
    DeploymentSource,
    DeploymentStatus,
    DeploymentStrategy,
)
from app.models.service import Service
from app.models.task import TaskStatus
from app.services.deployment_repository import DeploymentRepository
from app.services.health_checker import HealthChecker
from app.services.release_strategy import RolloutContext, execute_release_strategy
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

log = get_logger("deployment")

# 按 service 解析出用哪个 PipelineAdapter。生产按服务配置构造(Jenkins/GitLab),
# 缺省或未配置时抛错;测试注入 fake。
AdapterProvider = Callable[[Service], PipelineAdapter]

# 按 service 解析出发布策略执行上下文(§11)。返回 None 表示该服务不做控制面侧
# 策略铺开(仅靠 CI 内部);返回 RolloutContext 时按 (runtime, strategy) 执行。
# async:生产实现需读 placement 并为每个裸机放置点建 executor(异步 DB 访问)。
RolloutProvider = Callable[[Service], Awaitable[RolloutContext | None]]


@dataclass(frozen=True)
class DeployRequest:
    """一次部署请求的参数(§15.2 body:{version, strategy, env})。env 取自 service。

    version 与 artifact_id 二选一：
    - CI 模式：传 version，触发外部 CI 流水线（现有路径）。
    - artifact 直发模式：传 artifact_id，由 ArtifactDeploymentService 直接
      把制品送上 runtime，不调 CI，不调 release_strategy。
    """

    version: str | None = None
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True)
class RollbackTarget:
    id: str
    version: str | None
    artifact: str | None
    artifact_id: str | None
    git_sha: str | None
    scan_result_id: str | None
    strategy: DeploymentStrategy


class DeploymentService:
    """编排 UI 触发部署:落 deployment、驱动 CI、流转状态。"""

    def __init__(
        self,
        db: Database,
        *,
        adapter_provider: AdapterProvider,
        health_checker: HealthChecker | None = None,
        rollout_provider: RolloutProvider | None = None,
        auto_rollback_on_health_fail: bool = False,
        artifact_deployer: Any | None = None,
    ) -> None:
        self._db = db
        self._adapter_provider = adapter_provider
        self._health_checker = health_checker
        # 发布策略铺开(§11):按 service 解析出 RolloutContext,CI 触发成功后按
        # (runtime, strategy) 执行滚动/重建等。默认 None——未注入时保持原行为
        # (仅触发 CI,由 CI 内部铺开),不破坏既有部署路径与测试。
        self._rollout_provider = rollout_provider
        # 发布后健康检查失败自动回滚(§11.2):默认关闭。开启后健康检查未通过时,
        # 除标 FAILED 外再自动重部署上一版成功制品(留 rolled_back 闭环)。
        self._auto_rollback_on_health_fail = auto_rollback_on_health_fail
        # artifact 直接部署服务(artifact 直发 Task 4):注入后支持 artifact 模式
        # 部署。未注入时 artifact 请求抛错,保持原有 CI 路径不变。
        self._artifact_deployer = artifact_deployer

    async def run_deploy(
        self,
        *,
        task_id: str,
        service_id: str,
        request: DeployRequest,
        operator: str,
    ) -> None:
        """执行一次部署编排。全程不抛:结果落在 deployment 与 task 状态上。"""
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            deployment_id, health_check = await self._execute(service_id, request, operator)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("deploy_failed", service_id=service_id, error=message)
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        # 发布后健康检查(§11.1):配了 health_check 且注入了 checker 才跑。
        # 不健康 → deployment 与 task 均落 FAILED(RUNNING→FAILED 合法流转);
        # 健康 / 未配置 / 未注入 checker → 落 SUCCESS(保持现有行为)。
        if self._health_checker is not None and health_check:
            result = await self._health_checker.check(health_check)
            if not result.healthy:
                async with self._db.session() as session:
                    await DeploymentRepository(session).mark_status(
                        deployment_id, DeploymentStatus.FAILED
                    )
                    await TaskRepository(session).mark_result(
                        task_id,
                        TaskStatus.FAILED,
                        error=f"发布后健康检查未通过: {result.detail}",
                    )
                log.warning(
                    "deploy_unhealthy",
                    service_id=service_id,
                    deployment_id=deployment_id,
                    detail=result.detail,
                )
                # 健康检查失败自动回滚(§11.2):开关开启时重部署上一版成功制品。
                # 回滚自身失败不覆盖健康检查的 FAILED 结论,仅记日志(部署已判失败,
                # 回滚是补救;补救失败需人工介入,但原始失败结论保持不变)。
                if self._auto_rollback_on_health_fail:
                    await self._try_auto_rollback(service_id, operator, deployment_id)
                return

        async with self._db.session() as session:
            await DeploymentRepository(session).mark_status(deployment_id, DeploymentStatus.SUCCESS)
            # artifact 模式 version 从制品派生，CI 模式来自请求
            version_label = getattr(request, "artifact_id", None) or request.version
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"version": version_label}
            )

    async def _try_auto_rollback(
        self, service_id: str, operator: str, failed_deployment_id: str
    ) -> None:
        """健康检查失败后重部署该失败记录指向的上一版，不改写失败结论。"""
        try:
            async with self._db.session() as session:
                repo = DeploymentRepository(session)
                failed = await repo.get(failed_deployment_id)
                if failed.previous_deployment_id is None:
                    raise AppError(
                        "no_rollback_target",
                        "失败部署没有上一版本可恢复",
                        status_code=409,
                    )
                target = await repo.get(failed.previous_deployment_id)
                service = await ServiceRepository(session).get_service(service_id)
                if (
                    target.service_id != service_id
                    or target.env != service.env
                    or target.status not in {DeploymentStatus.SUCCESS, DeploymentStatus.ROLLED_BACK}
                ):
                    raise AppError(
                        "rollback_target_invalid",
                        "失败部署的上一版本不可恢复",
                        status_code=409,
                    )
                snapshot = self._rollback_snapshot(target)

            version = await self._redeploy_rollback_target(
                service=service,
                target=snapshot,
                operator=operator,
                previous_deployment_id=failed_deployment_id,
                rolled_back_deployment_id=None,
            )
            log.warning(
                "auto_rollback_after_unhealthy",
                service_id=service_id,
                failed_deployment_id=failed_deployment_id,
                rolled_back_to=version,
            )
        except Exception as exc:  # noqa: BLE001 —— 自动回滚失败不得影响主流程
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning(
                "auto_rollback_failed",
                service_id=service_id,
                failed_deployment_id=failed_deployment_id,
                error=message,
            )

    async def _execute(
        self, service_id: str, request: DeployRequest, operator: str
    ) -> tuple[str, dict | None]:
        """加载服务→落 running deployment→触发 CI。

        artifact 模式（request.artifact_id 存在）分派到 _execute_artifact；
        CI 模式（version 存在）走现有 CI 触发路径。

        CI 触发成功后 deployment 保持 RUNNING(不落终态),由 run_deploy 依据发布后
        健康检查再落 SUCCESS/FAILED——避免先落 SUCCESS 再翻 FAILED 的非法状态流转。
        返回 (deployment_id, 该 service 的 health_check 配置);无配置则第二项为 None。
        """
        # artifact 直接部署分支：不触发 CI，直接调 ArtifactDeploymentService
        if request.artifact_id is not None:
            return await self._execute_artifact(service_id, request, operator)

        # CI 模式分支（既有路径）：加载服务并取出编排所需字段
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            env = service.env
            health_check = service.health_check
            # 上一次成功部署,挂到 previous 支撑回滚链路
            previous = await DeploymentRepository(session).latest_successful(service_id, env=env)
            previous_id = previous.id if previous else None
            # 全链路关联(§9/§14.9):带 git_sha 时回填本次扫描结果 id,使部署详情能
            # 向前追溯到扫描结论。一个 sha 可能有多扫描器(各一条),取 scanner 序最靠前
            # 的一条作代表焊点;无扫描结果则留空。
            scan_result_id = None
            if request.git_sha:
                scans = await ScanResultRepository(session).list_for_git_sha(request.git_sha)
                if scans:
                    scan_result_id = scans[0].id
            adapter = self._adapter_provider(service)
            # 发布策略上下文在会话内解析(需读 runtime/runtime_ref,避免会话关闭后惰性访问);
            # 未注入 rollout_provider 时为 None,保持"仅触发 CI"的原行为。
            rollout_context = (
                await self._rollout_provider(service)
                if self._rollout_provider is not None
                else None
            )

        # 落一条 running 部署记录(独立事务,让前端/轮询立即可见)
        async with self._db.session() as session:
            deployment = await DeploymentRepository(session).create(
                service_id=service_id,
                env=env,
                source=DeploymentSource.UI_TRIGGERED,
                strategy=request.strategy,
                version=request.version,
                git_sha=request.git_sha,
                operator=operator,
                previous_deployment_id=previous_id,
                scan_result_id=scan_result_id,
            )
            deployment_id = deployment.id

        # 触发 CI:失败则把 deployment 落 failed 后向上抛(由 run_deploy 落 task.failed)
        try:
            run_id = await adapter.trigger(
                request.version, params={"VERSION": request.version, "ENV": env}
            )
        except Exception:
            async with self._db.session() as session:
                await DeploymentRepository(session).mark_status(
                    deployment_id, DeploymentStatus.FAILED
                )
            raise

        # 触发成功:回填 pipeline_id,deployment 暂留 RUNNING(健康检查后再落终态)
        async with self._db.session() as session:
            repo = DeploymentRepository(session)
            deployment = await repo.get(deployment_id)
            deployment.pipeline_id = run_id
            await session.flush()

        # 发布策略铺开(§11):该服务解析出了 RolloutContext 时,按 (runtime, strategy)
        # 执行滚动/重建等。上下文已在首个事务内解析(rollout_context),此处不再触碰
        # 已脱离会话的 service。失败落 deployment.failed 后上抛(由 run_deploy 落
        # task.failed),与 CI 触发失败同一语义。
        if rollout_context is not None:
            try:
                await execute_release_strategy(request.strategy, rollout_context)
            except Exception:
                async with self._db.session() as session:
                    await DeploymentRepository(session).mark_status(
                        deployment_id, DeploymentStatus.FAILED
                    )
                raise

        return (deployment_id, health_check)

    async def _execute_artifact(
        self, service_id: str, request: DeployRequest, operator: str
    ) -> tuple[str, dict | None]:
        """artifact 直接部署路径：resolve metadata → 落 running deployment → 调 deployer。

        不触发 CI，不调 release_strategy（artifact 直发已含 runtime 动作）。
        artifact_id / uri / version / git_sha 全部从 deployer.deploy() 返回值派生。
        """
        if self._artifact_deployer is None:
            raise AppError(
                "artifact_deployer_not_configured",
                "未配置 artifact 部署服务，无法执行 artifact 直接部署",
                status_code=501,
            )

        # 调 ArtifactDeploymentService.deploy 执行 artifact→runtime 动作
        deploy_input = await self._artifact_deployer.deploy(service_id, request.artifact_id)

        # 加载 service 元数据（env / health_check / previous）
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            env = service.env
            health_check = service.health_check
            previous = await DeploymentRepository(session).latest_successful(service_id, env=env)
            previous_id = previous.id if previous else None
            # git_sha 用于扫描关联：优先 deploy_input，否则取 request
            git_sha = deploy_input.git_sha or request.git_sha
            scan_result_id = None
            if git_sha:
                scans = await ScanResultRepository(session).list_for_git_sha(git_sha)
                if scans:
                    scan_result_id = scans[0].id

        # 落一条 running deployment（artifact 模式带 artifact_id / uri）
        async with self._db.session() as session:
            deployment = await DeploymentRepository(session).create(
                service_id=service_id,
                env=env,
                source=DeploymentSource.UI_TRIGGERED,
                strategy=request.strategy,
                version=deploy_input.version,
                artifact=deploy_input.uri,
                artifact_id=deploy_input.artifact_id,
                git_sha=git_sha,
                operator=operator,
                previous_deployment_id=previous_id,
                scan_result_id=scan_result_id,
            )
            deployment_id = deployment.id

        return (deployment_id, health_check)

    async def run_rollback(
        self,
        *,
        task_id: str,
        service_id: str,
        operator: str,
        target_deployment_id: str | None = None,
    ) -> None:
        """重部署指定历史快照；省略 target 时兼容沿 current.previous 解析。"""
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            version = await self._execute_rollback(
                service_id,
                operator,
                target_deployment_id=target_deployment_id,
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("rollback_failed", service_id=service_id, error=message)
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        async with self._db.session() as session:
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"version": version}
            )

    async def _execute_rollback(
        self,
        service_id: str,
        operator: str,
        *,
        target_deployment_id: str | None = None,
    ) -> str:
        """解析 current/target 后按 artifact 或 CI canonical owner 重部署。"""
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            current, target = await DeploymentRepository(session).resolve_rollback_target(
                service_id,
                env=service.env,
                target_deployment_id=target_deployment_id,
            )
            snapshot = self._rollback_snapshot(target)

        return await self._redeploy_rollback_target(
            service=service,
            target=snapshot,
            operator=operator,
            previous_deployment_id=current.id,
            rolled_back_deployment_id=current.id,
        )

    async def _redeploy_rollback_target(
        self,
        *,
        service: Service,
        target: RollbackTarget,
        operator: str,
        previous_deployment_id: str,
        rolled_back_deployment_id: str | None,
    ) -> str:
        adapter: PipelineAdapter | None = None
        if target.artifact_id is not None:
            if self._artifact_deployer is None:
                raise AppError(
                    "artifact_deployer_not_configured",
                    "未配置 artifact 部署服务，无法执行 artifact 回滚",
                    status_code=501,
                )
            await self._artifact_deployer.resolve(service.id, target.artifact_id)
        else:
            adapter = self._adapter_provider(service)
            if adapter is None:
                raise AppError(
                    "pipeline_not_configured",
                    "未配置 CI 流水线，无法执行回滚",
                    status_code=503,
                )

        async with self._db.session() as session:
            deployment = await DeploymentRepository(session).create(
                service_id=service.id,
                env=service.env,
                source=DeploymentSource.UI_TRIGGERED,
                strategy=target.strategy,
                version=target.version,
                artifact=target.artifact,
                artifact_id=target.artifact_id,
                git_sha=target.git_sha,
                scan_result_id=target.scan_result_id,
                operator=operator,
                previous_deployment_id=previous_deployment_id,
            )
            deployment_id = deployment.id

        run_id: str | None = None
        try:
            if target.artifact_id is not None:
                await self._artifact_deployer.deploy(service.id, target.artifact_id)
            else:
                run_id = await adapter.trigger(
                    target.version,
                    params={
                        "ARTIFACT": target.artifact or "",
                        "ENV": service.env,
                        "VERSION": target.version,
                    },
                )
        except Exception:
            async with self._db.session() as session:
                await DeploymentRepository(session).mark_status(
                    deployment_id, DeploymentStatus.FAILED
                )
            raise

        async with self._db.session() as session:
            repo = DeploymentRepository(session)
            deployment = await repo.get(deployment_id)
            if run_id is not None:
                deployment.pipeline_id = run_id
            await repo.mark_status(deployment_id, DeploymentStatus.SUCCESS)
            if rolled_back_deployment_id is not None:
                await repo.mark_status(
                    rolled_back_deployment_id,
                    DeploymentStatus.ROLLED_BACK,
                )

        return target.version or target.artifact_id or ""

    @staticmethod
    def _rollback_snapshot(deployment: Deployment) -> RollbackTarget:
        return RollbackTarget(
            id=deployment.id,
            version=deployment.version,
            artifact=deployment.artifact,
            artifact_id=deployment.artifact_id,
            git_sha=deployment.git_sha,
            scan_result_id=deployment.scan_result_id,
            strategy=deployment.strategy,
        )

    async def run_promotion(
        self,
        *,
        task_id: str,
        source_service_id: str,
        target_service_id: str,
        operator: str,
    ) -> None:
        """执行一次环境晋升编排。全程不抛:结果落在 deployment 与 task 状态上。

        晋升 = 取源环境(如 staging)最近一次成功部署的 artifact,在目标环境(如
        prod)以**同一制品**重新部署,不重构建(§10.3)——保证上线的与验证过的完全一致。
        """
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            version = await self._execute_promotion(source_service_id, target_service_id, operator)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning(
                "promotion_failed",
                source_service_id=source_service_id,
                target_service_id=target_service_id,
                error=message,
            )
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        async with self._db.session() as session:
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"version": version}
            )

    async def _execute_promotion(
        self, source_service_id: str, target_service_id: str, operator: str
    ) -> str:
        """取源环境最近成功部署的制品→在目标环境落新 running deployment→触发 CI→
        成功落 success。返回晋升的版本号。源无成功部署或目标服务不存在时抛错。"""
        async with self._db.session() as session:
            svc_repo = ServiceRepository(session)
            source = await svc_repo.get_service(source_service_id)
            target = await svc_repo.get_service(target_service_id)
            source_env = source.env
            target_env = target.env

            src_deploy = await DeploymentRepository(session).latest_successful(
                source_service_id, env=source_env
            )
            if src_deploy is None:
                raise AppError(
                    "no_promotion_source",
                    "源环境无成功部署,无可晋升的制品",
                    status_code=409,
                )
            artifact = src_deploy.artifact
            version = src_deploy.version or ""
            git_sha = src_deploy.git_sha
            # 目标环境上一次成功部署,挂 previous 支撑回滚链路
            previous = await DeploymentRepository(session).latest_successful(
                target_service_id, env=target_env
            )
            previous_id = previous.id if previous else None
            adapter = self._adapter_provider(target)

        # 目标环境落一条 running 晋升记录(同一制品,不重构建)
        async with self._db.session() as session:
            deployment = await DeploymentRepository(session).create(
                service_id=target_service_id,
                env=target_env,
                source=DeploymentSource.MANUAL,
                version=version,
                artifact=artifact,
                git_sha=git_sha,
                operator=operator,
                previous_deployment_id=previous_id,
            )
            deployment_id = deployment.id

        # 触发 CI 用同一制品部署;失败则新记录落 failed 并向上抛
        try:
            run_id = await adapter.trigger(
                version,
                params={
                    "ARTIFACT": artifact or "",
                    "ENV": target_env,
                    "VERSION": version,
                },
            )
        except Exception:
            async with self._db.session() as session:
                await DeploymentRepository(session).mark_status(
                    deployment_id, DeploymentStatus.FAILED
                )
            raise

        async with self._db.session() as session:
            repo = DeploymentRepository(session)
            deployment = await repo.get(deployment_id)
            deployment.pipeline_id = run_id
            await repo.mark_status(deployment_id, DeploymentStatus.SUCCESS)

        return version
