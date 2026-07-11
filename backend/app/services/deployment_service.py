"""UI 触发部署的编排核心(T2.3,设计 §8.1 模式 A)。

纯 async 编排:接收一个已落库的 deploy task,加载服务→落一条 deployment
(running, source=ui-triggered)→调 PipelineAdapter 触发 CI→据触发结果流转
deployment 与 task 状态。与传输层解耦,可被 FastAPI BackgroundTasks 直接 await。

设计要点:
- adapter_provider 按 service 返回对应 PipelineAdapter(生产按服务配置选
  Jenkins/GitLab,测试注入 fake),本服务不关心 pipeline 选型细节。
- deployment 与 task 双记录:deployment 是业务「焊点」(§14.3),task 是异步执行
  载体(供前端轮询)。两者状态同步流转。
- previous_deployment_id 挂到上一次成功部署,支撑一键回滚链路(§11.2)。
- 全程不抛:失败落在 deployment.failed + task.failed,错误摘要入 task。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.adapters.pipeline import PipelineAdapter
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.deployment import DeploymentSource, DeploymentStatus, DeploymentStrategy
from app.models.service import Service
from app.models.task import TaskStatus
from app.services.deployment_repository import DeploymentRepository
from app.services.health_checker import HealthChecker
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

log = get_logger("deployment")

# 按 service 解析出用哪个 PipelineAdapter。生产按服务配置构造(Jenkins/GitLab),
# 缺省或未配置时抛错;测试注入 fake。
AdapterProvider = Callable[[Service], PipelineAdapter]


@dataclass(frozen=True)
class DeployRequest:
    """一次部署请求的参数(§15.2 body:{version, strategy, env})。env 取自 service。"""

    version: str
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = None


class DeploymentService:
    """编排 UI 触发部署:落 deployment、驱动 CI、流转状态。"""

    def __init__(
        self,
        db: Database,
        *,
        adapter_provider: AdapterProvider,
        health_checker: HealthChecker | None = None,
    ) -> None:
        self._db = db
        self._adapter_provider = adapter_provider
        self._health_checker = health_checker

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
            deployment_id, health_check = await self._execute(
                service_id, request, operator
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning(
                "deploy_failed", service_id=service_id, error=message
            )
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(
                    task_id, TaskStatus.FAILED, error=message
                )
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
                return

        async with self._db.session() as session:
            await DeploymentRepository(session).mark_status(
                deployment_id, DeploymentStatus.SUCCESS
            )
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"version": request.version}
            )

    async def _execute(
        self, service_id: str, request: DeployRequest, operator: str
    ) -> tuple[str, dict | None]:
        """加载服务→落 running deployment→触发 CI。

        CI 触发成功后 deployment 保持 RUNNING(不落终态),由 run_deploy 依据发布后
        健康检查再落 SUCCESS/FAILED——避免先落 SUCCESS 再翻 FAILED 的非法状态流转。
        返回 (deployment_id, 该 service 的 health_check 配置);无配置则第二项为 None。
        """
        # 加载服务并取出编排所需字段(避免会话关闭后惰性访问)
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            env = service.env.value
            health_check = service.health_check
            # 上一次成功部署,挂到 previous 支撑回滚链路
            previous = await DeploymentRepository(session).latest_successful(
                service_id, env=env
            )
            previous_id = previous.id if previous else None
            adapter = self._adapter_provider(service)

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

        return (deployment_id, health_check)

    async def run_rollback(
        self,
        *,
        task_id: str,
        service_id: str,
        operator: str,
    ) -> None:
        """执行一次回滚编排。全程不抛:结果落在 deployment 与 task 状态上。

        回滚 = 重部署当前运行版(最近 success)的 artifact,不是撤销(§11.1):
        生成新 deployment(previous 指向被回滚的当前版),被回滚版落 rolled_back(§11.2)。
        """
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            version = await self._execute_rollback(service_id, operator)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("rollback_failed", service_id=service_id, error=message)
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(
                    task_id, TaskStatus.FAILED, error=message
                )
            return

        async with self._db.session() as session:
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"version": version}
            )

    async def _execute_rollback(self, service_id: str, operator: str) -> str:
        """加载服务→取当前运行版 artifact→落新 running deployment→触发 CI→
        成功后把被回滚的当前版落 rolled_back。返回重部署的版本号。无可回滚版本抛错。"""
        async with self._db.session() as session:
            service = await ServiceRepository(session).get_service(service_id)
            env = service.env.value
            current = await DeploymentRepository(session).latest_successful(
                service_id, env=env
            )
            if current is None:
                raise AppError(
                    "no_rollback_target", "无可回滚的历史成功部署", status_code=409
                )
            current_id = current.id
            artifact = current.artifact
            version = current.version or ""
            adapter = self._adapter_provider(service)

        # 落一条 running 回滚记录(previous 指向被回滚的当前版)
        async with self._db.session() as session:
            deployment = await DeploymentRepository(session).create(
                service_id=service_id,
                env=env,
                source=DeploymentSource.UI_TRIGGERED,
                version=version,
                artifact=artifact,
                operator=operator,
                previous_deployment_id=current_id,
            )
            deployment_id = deployment.id

        # 触发 CI:失败则新记录落 failed 并向上抛(不闭环旧版)
        try:
            run_id = await adapter.trigger(
                version, params={"ARTIFACT": artifact or "", "ENV": env, "VERSION": version}
            )
        except Exception:
            async with self._db.session() as session:
                await DeploymentRepository(session).mark_status(
                    deployment_id, DeploymentStatus.FAILED
                )
            raise

        # 触发成功:回填 pipeline_id、新记录落 success、被回滚的当前版落 rolled_back(闭环)
        async with self._db.session() as session:
            repo = DeploymentRepository(session)
            deployment = await repo.get(deployment_id)
            deployment.pipeline_id = run_id
            await repo.mark_status(deployment_id, DeploymentStatus.SUCCESS)
            await repo.mark_status(current_id, DeploymentStatus.ROLLED_BACK)

        return version
