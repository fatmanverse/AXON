"""发布策略 RolloutContext 生产工厂(T3.6/T3.7 生产接线)。

DeploymentService 的 rollout_provider 之前生产从不注入 → execute_release_strategy
在真实部署路径永不执行(策略层只在测试里活着)。本工厂补上生产实现:按
service.runtime 现组装一个 RolloutContext——

- **k8s**:用启动时加载的 k8s_api_factory 造 K8sRuntime 适配器,namespace/workload
  取自 runtime_ref;rolling→rollout restart、recreate→scale 0→N 由策略层执行。
- **裸机(systemd/docker)**:为每个 placement 的 server 建 Executor,包成
  BareMetalTarget(adapter=对应 runtime 适配器, ref=runtime_ref 的目标键);
  rolling→逐放置分批重启、recreate→全停全起。

诚实边界(与 release_strategy 一致):
- k8s 未开启(k8s_api_factory 为 None)→ 返回 None,该服务不做控制面侧策略铺开
  (退回"仅触发 CI"),而非静默假装成功。
- canary/blue-green 的负载均衡编排端(LoadBalancerLike)MVP 未接:不注入
  load_balancer,策略层对裸机 canary/蓝绿按既有逻辑报 501(不静默降级)。

provider 是 async:裸机分支需读 placement 与 server(异步 DB),并为每个放置点建
executor。返回的 RolloutContext 内的 adapter/executor 不持有会话,可在会话关闭后使用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.adapters.k8s_runtime import K8sRuntime
from app.adapters.runtime_registry import SSH_RUNTIMES
from app.core.config import Settings
from app.core.db import Database
from app.core.secrets import SecretStore
from app.models.service import Runtime, Service
from app.services.executor_factory import build_executor_for_server
from app.services.k8s_client import K8sApiFactory
from app.services.release_strategy import BareMetalTarget, RolloutContext
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository

# 与 DeploymentService.RolloutProvider 一致:按 service 异步解析出 RolloutContext 或 None。
RolloutProvider = Callable[[Service], Awaitable[RolloutContext | None]]


def build_rollout_provider(
    db: Database,
    secrets: SecretStore,
    settings: Settings,
    *,
    k8s_api_factory: K8sApiFactory | None = None,
    connector: Callable[..., object] | None = None,
) -> RolloutProvider:
    """构造生产 rollout provider。

    k8s_api_factory 为 None 时对 k8s 服务返回 None(不做控制面侧铺开);裸机服务
    始终按 placement 组装 BareMetalTarget。返回的 provider 每次调用现组装上下文
    (不缓存),保证读到最新 placement。
    """

    async def _provider(service: Service) -> RolloutContext | None:
        if service.runtime == Runtime.K8S:
            return _build_k8s_context(service, k8s_api_factory, settings)
        return await _build_bare_context(service)

    def _build_k8s_context(
        service: Service,
        factory: K8sApiFactory | None,
        settings: Settings,
    ) -> RolloutContext | None:
        # 未启用 k8s client:不做控制面侧策略铺开(退回仅触发 CI),而非静默假装。
        if factory is None:
            return None
        runtime_ref = service.runtime_ref or {}
        namespace = runtime_ref.get("namespace")
        workload = runtime_ref.get("workload")
        if not namespace or not workload:
            # runtime_ref 不完整:无法定位工作负载,交回 CI 内部铺开。
            return None
        return RolloutContext(
            runtime=Runtime.K8S,
            k8s_adapter=K8sRuntime(factory()),
            namespace=namespace,
            workload=workload,
            replicas=settings.k8s_default_replicas,
        )

    async def _build_bare_context(service: Service) -> RolloutContext | None:
        spec = SSH_RUNTIMES.get(service.runtime)
        if spec is None:
            return None
        runtime_ref = service.runtime_ref or {}
        ref = runtime_ref.get(spec.ref_key)
        if not ref:
            return None

        # 读放置点与其 server,为每个放置建 executor + runtime 适配器
        async with db.session() as session:
            svc_repo = ServiceRepository(session)
            placements = list(await svc_repo.list_placements(service.id))
            server_repo = ServerRepository(session)
            servers = [
                await server_repo.get(p.server_id) if p.server_id else None for p in placements
            ]

        targets: list[BareMetalTarget] = []
        for server in servers:
            executor = build_executor_for_server(server, secrets, connector=connector)
            targets.append(BareMetalTarget(adapter=spec.adapter_cls(executor), ref=ref))

        if not targets:
            return None
        return RolloutContext(runtime=service.runtime, bare_targets=targets)

    return _provider
