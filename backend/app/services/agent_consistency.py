"""Agent 断连一致性与脑裂防护的纯决策逻辑(T4.4,设计 §5.4)。

控制面下发命令给 Agent 是分布式难题:连不上 ≠ Agent 已死,可能只是网络分区。
本模块把 §5.4 的一致性语义收敛成一组**纯决策函数**(无 I/O、无状态),与传输层
(gRPC/WebSocket)、DB 解耦,便于穷举边界单测。真实通道(AgentGateway)据此推进
task 状态机、决定离线策略、校验 fencing、算重连退避。

设计取舍(§5.4):
- at-least-once + 幂等,不追求成本高昂的 exactly-once:命令尽量设计成幂等
  (restart、deploy(artifact=X) 近幂等),重连补执行不造成二次伤害。
- 两段 ACK:received(送达)不推进终态,只有 result(执行完)才落 success/failed。
- 超时判 unknown 而非 failed:可能已成功执行,待重连核对或人工介入。
- 离线命令按环境与危险度分档:prod 高危直接拒绝,低危短暂排队 + TTL。
- fencing token 单调递增:旧 token 执行被拒,保证任一时刻只有一个通道能动同一目标。
"""

from __future__ import annotations

import random
from enum import StrEnum

from app.models.task import TaskStatus


class AckKind(StrEnum):
    """Agent 回传的 ACK 类型(§5.4 ①)。"""

    RECEIVED = "received"  # 命令送达 Agent(尚未执行完)
    RESULT = "result"  # 命令执行完毕(带成功/失败)


class OfflineCommandDecision(StrEnum):
    """Agent 离线时对一条命令的处置(§5.4 ⑤)。"""

    DISPATCH = "dispatch"  # 在线:正常下发
    REJECT = "reject"  # 离线 + 生产高危:直接拒绝
    QUEUE_WITH_TTL = "queue_with_ttl"  # 离线 + 低危:短暂排队,TTL 过期作废


# 生产环境视为高危的动作(离线时拒绝排队,避免过期变更数小时后突然执行)。
_PROD_HIGH_RISK_ACTIONS: frozenset[str] = frozenset({"deploy", "delete", "rollback"})


def resolve_ack_status(kind: AckKind, *, ok: bool | None) -> TaskStatus:
    """据 ACK 类型推进 task 状态(§5.4 ①「下发≠成功」)。

    received:仅表示送达,任务保持 running 等 result,绝不"下发即标记成功"。
    result:执行完毕,据 ok 落 success/failed。
    """
    if kind == AckKind.RECEIVED:
        return TaskStatus.RUNNING
    return TaskStatus.SUCCESS if ok else TaskStatus.FAILED


def resolve_timeout_status() -> TaskStatus:
    """命令超时的状态(§5.4 ④):判 unknown 而非 failed。

    超时下命令可能已在目标机成功执行,武断判 failed 会误导回滚/重试。判 unknown
    交由重连补报核对或人工介入。task 状态机已支持 unknown→success/failed 再落定。
    """
    return TaskStatus.UNKNOWN


def decide_offline_command(
    *, env: str, action: str, agent_online: bool
) -> OfflineCommandDecision:
    """Agent 离线时对命令的处置分档(§5.4 ⑤)。

    在线一律正常下发;离线时按环境与危险度分档:prod 高危(deploy/delete/rollback)
    直接拒绝(避免 Agent 数小时后重连突然执行过期的生产变更),其余低危短暂排队 +
    TTL 过期作废。
    """
    if agent_online:
        return OfflineCommandDecision.DISPATCH
    if env == "prod" and action in _PROD_HIGH_RISK_ACTIONS:
        return OfflineCommandDecision.REJECT
    return OfflineCommandDecision.QUEUE_WITH_TTL


def fence_allows(*, current_fence: int, command_fence: int) -> bool:
    """fencing token 校验(§5.4 ⑥):命令持有的 token 不低于当前租约才放行。

    同一 service 的写操作持单调递增 fence token。SSH fallback 执行前必须确认自己
    的 token 不比当前租约旧——旧 token 被拒,防止网络分区下 SSH 与 Agent 双通道
    同时动同一目标造成双重执行。
    """
    return command_fence >= current_fence


def next_backoff_seconds(
    *, attempt: int, base: float, cap: float, jitter: float = 0.2
) -> float:
    """重连退避间隔(§5.4 ⑦):指数退避 + jitter,封顶在 cap。

    控制面重启时几百个 Agent 同时重连会打爆,故 Agent 重连必须指数退避
    (base * 2^attempt)并加抖动打散重连风暴。jitter 为相对比例(0.2 = 上浮 ≤20%)。
    """
    if attempt < 0:
        raise ValueError("attempt 不能为负")
    raw = base * (2**attempt)
    capped = min(raw, cap)
    if jitter <= 0:
        return capped
    return capped * (1 + random.random() * jitter)  # noqa: S311 (退避抖动非密码学用途)
