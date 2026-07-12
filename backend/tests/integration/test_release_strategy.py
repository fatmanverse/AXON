"""发布策略执行验收(T3.6,设计 §11)。

覆盖 ReleaseStrategyExecutor 按 (runtime, strategy) 多态执行「如何铺开新版本」:
- k8s rolling:走原生 rollout restart(patch restartedAt annotation)。
- k8s recreate:scale 0 → scale 回目标副本(对应 k8s Recreate 语义)。
- 裸机 rolling:逐个 placement 分批重启(不一次性全停)。
- 裸机 recreate:先全停,再全起(有短暂停机)。
- canary / blue-green:k8s 原生不支持(需 Argo Rollouts / service mesh),
  抽象层预留但明确抛 not_implemented,不伪造。

用 fake k8s adapter 与 fake 裸机适配器,记录调用序列,断言编排顺序正确。
"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.models.deployment import DeploymentStrategy
from app.models.service import Runtime
from app.services.release_strategy import (
    BareMetalTarget,
    RolloutContext,
    execute_release_strategy,
)


class FakeK8s:
    """记录 k8s 适配器调用序列的假实现。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def restart(self, namespace: str, workload: str) -> None:
        self.calls.append(("restart", (namespace, workload)))

    async def scale(self, namespace: str, workload: str, replicas: int) -> None:
        self.calls.append(("scale", (namespace, workload, replicas)))


class FakeBareAdapter:
    """记录单个裸机目标 restart/stop/start 调用的假适配器。"""

    def __init__(self, name: str, log: list[tuple[str, str]]) -> None:
        self._name = name
        self._log = log

    async def restart(self, target: str) -> None:
        self._log.append(("restart", self._name))

    async def stop(self, target: str) -> None:
        self._log.append(("stop", self._name))

    async def start(self, target: str) -> None:
        self._log.append(("start", self._name))


async def test_k8s_rolling_uses_native_rollout():
    k8s = FakeK8s()
    ctx = RolloutContext(
        runtime=Runtime.K8S,
        k8s_adapter=k8s,
        namespace="prod",
        workload="billing",
        replicas=3,
    )
    await execute_release_strategy(DeploymentStrategy.ROLLING, ctx)
    assert k8s.calls == [("restart", ("prod", "billing"))]


async def test_k8s_recreate_scales_down_then_up():
    k8s = FakeK8s()
    ctx = RolloutContext(
        runtime=Runtime.K8S,
        k8s_adapter=k8s,
        namespace="prod",
        workload="billing",
        replicas=3,
    )
    await execute_release_strategy(DeploymentStrategy.RECREATE, ctx)
    # 先缩到 0,再拉回目标副本(顺序严格)
    assert k8s.calls == [
        ("scale", ("prod", "billing", 0)),
        ("scale", ("prod", "billing", 3)),
    ]


async def test_bare_metal_rolling_restarts_each_placement():
    log: list[tuple[str, str]] = []
    targets = [
        BareMetalTarget(adapter=FakeBareAdapter("h1", log), ref="billing.service"),
        BareMetalTarget(adapter=FakeBareAdapter("h2", log), ref="billing.service"),
    ]
    ctx = RolloutContext(runtime=Runtime.SYSTEMD, bare_targets=targets)
    await execute_release_strategy(DeploymentStrategy.ROLLING, ctx)
    # 逐台重启(分批),每台一次 restart
    assert log == [("restart", "h1"), ("restart", "h2")]


async def test_bare_metal_recreate_stops_all_then_starts_all():
    log: list[tuple[str, str]] = []
    targets = [
        BareMetalTarget(adapter=FakeBareAdapter("h1", log), ref="a.service"),
        BareMetalTarget(adapter=FakeBareAdapter("h2", log), ref="a.service"),
    ]
    ctx = RolloutContext(runtime=Runtime.SYSTEMD, bare_targets=targets)
    await execute_release_strategy(DeploymentStrategy.RECREATE, ctx)
    # 先全停,再全起(有停机窗口)
    assert log == [
        ("stop", "h1"),
        ("stop", "h2"),
        ("start", "h1"),
        ("start", "h2"),
    ]


async def test_canary_not_supported_on_k8s_without_argo():
    k8s = FakeK8s()
    ctx = RolloutContext(
        runtime=Runtime.K8S,
        k8s_adapter=k8s,
        namespace="prod",
        workload="billing",
        replicas=3,
    )
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.CANARY, ctx)
    assert exc.value.status_code == 501
    assert k8s.calls == []


async def test_blue_green_without_lb_reports_needs_lb():
    """裸机蓝绿无 LB 注入 → 明确报 501(裸机蓝绿确需负载均衡,不静默降级)。"""
    log: list[tuple[str, str]] = []
    targets = [BareMetalTarget(adapter=FakeBareAdapter("h1", log), ref="a.service")]
    ctx = RolloutContext(runtime=Runtime.SYSTEMD, bare_targets=targets)
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.BLUE_GREEN, ctx)
    assert exc.value.status_code == 501


async def test_canary_without_lb_reports_needs_lb():
    log: list[tuple[str, str]] = []
    targets = [BareMetalTarget(adapter=FakeBareAdapter("h1", log), ref="a.service")]
    ctx = RolloutContext(runtime=Runtime.SYSTEMD, bare_targets=targets)
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.CANARY, ctx)
    assert exc.value.status_code == 501


class FakeLoadBalancer:
    """记录 LB 编排调用序列的假实现。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def set_weight(self, target: str, weight: int) -> None:
        self.calls.append(("set_weight", target, weight))

    async def switch_upstream(self, target: str, upstream: str) -> None:
        self.calls.append(("switch_upstream", target, upstream))


async def test_bare_canary_restarts_then_ramps_weight():
    """裸机金丝雀有 LB:先重启实例上新版,再按阶梯放量权重。"""
    log: list[tuple[str, str]] = []
    lb = FakeLoadBalancer()
    targets = [
        BareMetalTarget(adapter=FakeBareAdapter("h1", log), ref="a.service"),
        BareMetalTarget(adapter=FakeBareAdapter("h2", log), ref="a.service"),
    ]
    ctx = RolloutContext(
        runtime=Runtime.SYSTEMD,
        bare_targets=targets,
        load_balancer=lb,
        upstream_ref="a-upstream",
        canary_steps=(10, 50, 100),
    )
    await execute_release_strategy(DeploymentStrategy.CANARY, ctx)
    # 先重启两台上新版
    assert log == [("restart", "h1"), ("restart", "h2")]
    # 再按阶梯放量
    assert lb.calls == [
        ("set_weight", "a-upstream", 10),
        ("set_weight", "a-upstream", 50),
        ("set_weight", "a-upstream", 100),
    ]


async def test_bare_blue_green_starts_green_then_switches():
    """裸机蓝绿有 LB:绿组实例起好后,上游瞬时切到新组。"""
    log: list[tuple[str, str]] = []
    lb = FakeLoadBalancer()
    targets = [BareMetalTarget(adapter=FakeBareAdapter("green", log), ref="a.service")]
    ctx = RolloutContext(
        runtime=Runtime.SYSTEMD,
        bare_targets=targets,
        load_balancer=lb,
        upstream_ref="a-upstream",
        new_upstream="green-pool",
    )
    await execute_release_strategy(DeploymentStrategy.BLUE_GREEN, ctx)
    assert log == [("start", "green")]
    assert lb.calls == [("switch_upstream", "a-upstream", "green-pool")]


async def test_k8s_canary_still_needs_argo():
    """k8s 侧 canary 仍需 Argo Rollouts(本层不接 CRD),继续报 501。"""
    k8s = FakeK8s()
    ctx = RolloutContext(
        runtime=Runtime.K8S, k8s_adapter=k8s, namespace="p", workload="w", replicas=2
    )
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.CANARY, ctx)
    assert exc.value.status_code == 501


async def test_k8s_rolling_requires_adapter():
    ctx = RolloutContext(runtime=Runtime.K8S, namespace="p", workload="w", replicas=1)
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.ROLLING, ctx)
    assert exc.value.status_code == 501


async def test_bare_metal_rolling_requires_targets():
    ctx = RolloutContext(runtime=Runtime.SYSTEMD, bare_targets=[])
    with pytest.raises(AppError) as exc:
        await execute_release_strategy(DeploymentStrategy.ROLLING, ctx)
    assert exc.value.status_code == 409
