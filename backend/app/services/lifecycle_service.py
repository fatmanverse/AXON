"""服务生命周期执行核心(T1.10,设计 §5.1 / §15.2)。

纯 async 的执行核心:接收一个已落库的 task,按 service.runtime 路由到对应
运行时适配器,对该服务的每个 placement 执行动作,并据结果流转 task 状态
(running → success / failed)。

设计要点:
- 与传输层解耦:本服务只依赖 Database、SecretStore 与可注入的 connector,
  既可被 FastAPI BackgroundTasks 直接 await,也可被 Celery task 用
  asyncio.run 包装,MVP 不强依赖 Redis/worker。
- 执行器按 server.access_mode 选择:SSH 走 SSHExecutor,Agent 走
  AgentGateway(当前抛未接入错误,落 failed,不影响 task 机制,§5.3)。
- 状态分两段提交:先独立事务标记 running(让轮询立即可见),执行完再新
  事务落终态,避免长事务占用连接。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.agent_gateway_registry import AgentGatewayRegistry
from app.adapters.executor import Executor
from app.adapters.k8s_runtime import AppsV1ApiLike, K8sRuntime
from app.adapters.runtime_registry import SSH_RUNTIMES
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.server import Server
from app.models.service import Runtime, ServicePlacement
from app.models.task import TaskStatus, TaskType
from app.services.executor_factory import build_executor_for_server
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

log = get_logger("lifecycle")

# task 动作 → 运行时适配器方法名。生命周期四动作共用同一映射(systemd/docker
# 适配器方法名一致);runtime 差异只在适配器类与 runtime_ref 的目标键。
_LIFECYCLE_METHODS: dict[TaskType, str] = {
    TaskType.START: "start",
    TaskType.STOP: "stop",
    TaskType.RESTART: "restart",
    TaskType.DELETE: "delete",
}


class LifecycleService:
    """按 runtime 多态执行服务生命周期动作,并驱动 task 状态机。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
        k8s_api_factory: Callable[[], AppsV1ApiLike] | None = None,
        agent_registry: AgentGatewayRegistry | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._connector = connector
        # k8s 走 client 而非 SSH;生产按需构造真实 AppsV1Api,测试注入 fake。
        # 缺省为 None:未配置 k8s client 时对 k8s 服务的动作会明确报错而非静默。
        self._k8s_api_factory = k8s_api_factory
        # agent 模式:注入注册表后 agent 服务器动作走真实 AgentGateway(§5.3);
        # 未注入(纯 SSH/未开 gRPC)时退回占位,agent 动作明确报 501,不影响 SSH。
        self._agent_registry = agent_registry

    async def run_action(self, *, task_id: str, service_id: str, action: TaskType) -> None:
        """执行一次生命周期动作。全程不抛:结果落在 task 状态上。"""
        # 第一段事务:标记 running,让轮询立即看到状态推进
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            await self._execute(service_id, action)
        except Exception as exc:  # 执行失败:落 failed,错误摘要入 task
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning(
                "lifecycle_action_failed",
                service_id=service_id,
                action=action.value,
                error=message,
            )
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        async with self._db.session() as session:
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"action": action.value}
            )

    async def _execute(self, service_id: str, action: TaskType) -> None:
        """加载服务与放置,按 runtime 对每个放置执行动作。任一失败即抛。"""
        async with self._db.session() as session:
            svc_repo = ServiceRepository(session)
            service = await svc_repo.get_service(service_id)
            placements = list(await svc_repo.list_placements(service_id))
            # 提前取出每个放置的 server(避免会话关闭后惰性加载失效)
            server_repo = ServerRepository(session)
            targets: list[tuple[ServicePlacement, Server | None]] = []
            for placement in placements:
                server = await server_repo.get(placement.server_id) if placement.server_id else None
                targets.append((placement, server))
            runtime = service.runtime
            runtime_ref = dict(service.runtime_ref or {})

        # k8s 不经 server/SSH:工作负载由 runtime_ref(namespace/workload)定位,
        # 集群侧自管副本分布,无需遍历 placement。单列一条分支执行。
        if runtime == Runtime.K8S:
            await self._dispatch_k8s(runtime_ref, action)
            return

        if not targets:
            raise AppError(
                "no_placement",
                "服务没有任何放置点,无法执行生命周期动作",
                status_code=409,
            )

        for _placement, server in targets:
            executor = self._build_executor(server)
            await self._dispatch(executor, runtime, runtime_ref, action)

    def _build_executor(self, server: Server | None) -> Executor:
        """按 server.access_mode 选择执行器(共享工厂,与配置下发一致)。"""
        return build_executor_for_server(
            server,
            self._secrets,
            connector=self._connector,
            agent_registry=self._agent_registry,
        )

    async def _dispatch(
        self,
        executor: Executor,
        runtime: Runtime,
        runtime_ref: dict[str, Any],
        action: TaskType,
    ) -> None:
        """把动作按 runtime 翻译成具体命令执行。MVP 支持 systemd/docker;k8s 待 T1.9 补齐。"""
        spec = SSH_RUNTIMES.get(runtime)
        if spec is None:
            # k8s 走 client 而非 Executor,在 T1.9 补齐;其余 runtime 显式拒绝而非静默
            raise AppError(
                "runtime_not_implemented",
                f"运行时 {runtime.value} 的生命周期动作尚未实现",
                status_code=501,
            )

        method_name = _LIFECYCLE_METHODS.get(action)
        if method_name is None:
            raise AppError(
                "unsupported_action",
                f"{runtime.value} 运行时不支持动作: {action.value}",
                status_code=400,
            )

        target = runtime_ref.get(spec.ref_key)
        if not target:
            raise AppError(
                "invalid_runtime_ref",
                f"{runtime.value} 服务的 runtime_ref 缺少 {spec.ref_key}",
                status_code=400,
            )

        adapter = spec.adapter_cls(executor)
        await getattr(adapter, method_name)(target)

    async def _dispatch_k8s(self, runtime_ref: dict[str, Any], action: TaskType) -> None:
        """k8s 分支:经注入的 client 对 Deployment 执行动作。"""
        if self._k8s_api_factory is None:
            raise AppError(
                "runtime_not_implemented",
                "未配置 k8s client,无法对 k8s 服务执行生命周期动作",
                status_code=501,
            )

        method_name = _LIFECYCLE_METHODS.get(action)
        if method_name is None:
            raise AppError(
                "unsupported_action",
                f"k8s 运行时不支持动作: {action.value}",
                status_code=400,
            )

        namespace = runtime_ref.get("namespace")
        workload = runtime_ref.get("workload")
        if not namespace or not workload:
            raise AppError(
                "invalid_runtime_ref",
                "k8s 服务的 runtime_ref 缺少 namespace 或 workload",
                status_code=400,
            )

        adapter = K8sRuntime(self._k8s_api_factory())
        await getattr(adapter, method_name)(namespace, workload)
