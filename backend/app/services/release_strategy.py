"""发布策略执行(T3.6,设计 §11)。

把「如何把新版本铺开」从部署编排中抽出,按 (runtime, strategy) 多态执行。设计
§11 的四策略在不同 runtime 有不同落地边界,这里诚实区分「已实现」与「需外部件」:

| 策略      | k8s                              | 裸机(systemd/docker)        |
|-----------|----------------------------------|-----------------------------|
| rolling   | 原生 rollout restart(逐 Pod 滚动)| 逐 placement 分批重启        |
| recreate  | scale 0 → scale 回目标副本        | 全停 → 全起(有停机窗口)     |
| canary    | 需 Argo Rollouts / service mesh  | 需负载权重编排               |
| blue-green| 需切 Service selector / 双环境    | 需负载均衡切上游             |

canary/blue-green 不做「假实现」:k8s 原生无金丝雀/蓝绿(需 Argo Rollouts 或
服务网格),裸机需负载均衡权重编排——两者都超出本层能力,故明确抛 501,由上层
提示用户「该策略需接入 Argo Rollouts / 负载均衡」,而非静默降级成 rolling。

本模块只依赖注入的适配器(k8s adapter / 裸机适配器),不碰 DB、不建连接,
纯编排,便于单测断言调用序列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import AppError
from app.models.deployment import DeploymentStrategy
from app.models.service import Runtime


class K8sRolloutLike(Protocol):
    """发布策略用到的 k8s 适配器子集(K8sRuntime 已满足)。"""

    async def restart(self, namespace: str, workload: str) -> None: ...
    async def scale(self, namespace: str, workload: str, replicas: int) -> None: ...


class BareRuntimeLike(Protocol):
    """发布策略用到的裸机适配器子集(Systemd/DockerRuntime 已满足)。"""

    async def restart(self, target: str) -> None: ...
    async def stop(self, target: str) -> None: ...
    async def start(self, target: str) -> None: ...


@dataclass(frozen=True)
class BareMetalTarget:
    """一个裸机放置点:绑定其运行时适配器与动作目标(unit/container 名)。"""

    adapter: BareRuntimeLike
    ref: str


@dataclass(frozen=True)
class RolloutContext:
    """发布执行所需的全部上下文,按 runtime 取用不同字段。

    k8s 用 k8s_adapter + namespace/workload/replicas;裸机用 bare_targets。
    由上层(部署编排)按 service.runtime 组装,策略层不关心它们如何构造。
    """

    runtime: Runtime
    k8s_adapter: K8sRolloutLike | None = None
    namespace: str | None = None
    workload: str | None = None
    replicas: int = 1
    bare_targets: list[BareMetalTarget] = field(default_factory=list)


def _needs_argo(strategy: DeploymentStrategy) -> AppError:
    return AppError(
        "strategy_not_implemented",
        f"{strategy.value} 策略需接入 Argo Rollouts / 服务网格(k8s)或负载均衡"
        "权重编排(裸机),当前未实现;可改用 rolling / recreate",
        status_code=501,
    )


async def execute_release_strategy(
    strategy: DeploymentStrategy, ctx: RolloutContext
) -> None:
    """按 (runtime, strategy) 执行发布铺开。不支持的组合抛 AppError(501/409)。"""
    if ctx.runtime == Runtime.K8S:
        await _execute_k8s(strategy, ctx)
        return
    await _execute_bare(strategy, ctx)


async def _execute_k8s(strategy: DeploymentStrategy, ctx: RolloutContext) -> None:
    if ctx.k8s_adapter is None or not ctx.namespace or not ctx.workload:
        raise AppError(
            "strategy_not_implemented",
            "k8s 发布策略需注入 k8s client 与 namespace/workload",
            status_code=501,
        )

    if strategy == DeploymentStrategy.ROLLING:
        # k8s 原生滚动:rollout restart 逐 Pod 替换(§11 Deployment 原生滚动)
        await ctx.k8s_adapter.restart(ctx.namespace, ctx.workload)
    elif strategy == DeploymentStrategy.RECREATE:
        # 停旧起新:scale 到 0 再拉回目标副本(对应 k8s Recreate 语义)
        await ctx.k8s_adapter.scale(ctx.namespace, ctx.workload, 0)
        await ctx.k8s_adapter.scale(ctx.namespace, ctx.workload, ctx.replicas)
    else:
        raise _needs_argo(strategy)


async def _execute_bare(strategy: DeploymentStrategy, ctx: RolloutContext) -> None:
    if not ctx.bare_targets:
        raise AppError(
            "no_placement",
            "服务没有任何放置点,无法执行发布策略",
            status_code=409,
        )

    if strategy == DeploymentStrategy.ROLLING:
        # 分批重启:逐 placement 重启,不一次性全停(保留其余实例在线)
        for target in ctx.bare_targets:
            await target.adapter.restart(target.ref)
    elif strategy == DeploymentStrategy.RECREATE:
        # 全停再全起:有短暂停机窗口(§11 recreate 语义)
        for target in ctx.bare_targets:
            await target.adapter.stop(target.ref)
        for target in ctx.bare_targets:
            await target.adapter.start(target.ref)
    else:
        raise _needs_argo(strategy)
