"""Agent 断连一致性与脑裂防护的决策逻辑验收(T4.4,设计 §5.4)。

分布式控制面下发命令给 Agent 的核心难题:控制面无法区分"没送达/送达没执行/
执行了没回结果"。这里验证一套 at-least-once + 幂等的纯决策逻辑(与传输/DB
解耦,便于穷举边界):

§5.4 ① 两段 ACK:下发≠成功,received 不推进终态,只有 result 推进。
§5.4 ④ 超时语义:命令超时判 unknown(可能已执行),不武断判 failed。
§5.4 ⑤ 断连期命令分档:prod 高危离线直接拒绝;低危可短暂排队 + TTL 过期作废。
§5.4 ⑥ fencing token:单调递增,旧 token 的执行被拒(防 SSH fallback 双执行)。
§5.4 ⑦ 重连退避:指数退避 + jitter,退避上限封顶。

用纯函数 + 冻结时钟,不触真实网络/DB。
"""

from __future__ import annotations

import pytest

from app.models.task import TaskStatus
from app.services.agent_consistency import (
    AckKind,
    OfflineCommandDecision,
    decide_offline_command,
    fence_allows,
    next_backoff_seconds,
    resolve_ack_status,
    resolve_timeout_status,
)


# ── §5.4 ① 两段 ACK ─────────────────────────────────────────────
def test_received_ack_keeps_running():
    # received 只表示送达,不推进终态,任务保持 running 等 result
    assert resolve_ack_status(AckKind.RECEIVED, ok=None) == TaskStatus.RUNNING


def test_result_ack_success_marks_success():
    assert resolve_ack_status(AckKind.RESULT, ok=True) == TaskStatus.SUCCESS


def test_result_ack_failure_marks_failed():
    assert resolve_ack_status(AckKind.RESULT, ok=False) == TaskStatus.FAILED


# ── §5.4 ④ 超时判 unknown ───────────────────────────────────────
def test_timeout_marks_unknown_not_failed():
    # 超时可能已执行,判 unknown 待核对,绝不武断判 failed
    assert resolve_timeout_status() == TaskStatus.UNKNOWN


# ── §5.4 ⑤ 断连期命令分档 ───────────────────────────────────────
def test_offline_prod_high_risk_rejected():
    # prod 高危(deploy/delete)离线直接拒绝,不排队——避免数小时后重连执行过期变更
    d = decide_offline_command(env="prod", action="deploy", agent_online=False)
    assert d == OfflineCommandDecision.REJECT


def test_offline_prod_delete_rejected():
    d = decide_offline_command(env="prod", action="delete", agent_online=False)
    assert d == OfflineCommandDecision.REJECT


def test_offline_low_risk_queued_with_ttl():
    # 非生产 restart / status 拉取:可短暂排队 + TTL 过期作废
    d = decide_offline_command(env="dev", action="restart", agent_online=False)
    assert d == OfflineCommandDecision.QUEUE_WITH_TTL


def test_online_always_dispatches():
    # Agent 在线:正常下发,不受分档影响
    d = decide_offline_command(env="prod", action="deploy", agent_online=True)
    assert d == OfflineCommandDecision.DISPATCH


# ── §5.4 ⑥ fencing token ────────────────────────────────────────
def test_fence_allows_newer_token():
    # 持有 token 不低于当前租约的执行被放行
    assert fence_allows(current_fence=5, command_fence=5) is True
    assert fence_allows(current_fence=5, command_fence=6) is True


def test_fence_rejects_stale_token():
    # 旧 token(低于当前租约)被拒:防 SSH fallback 用过期租约二次执行
    assert fence_allows(current_fence=5, command_fence=4) is False


# ── §5.4 ⑦ 重连退避 ─────────────────────────────────────────────
def test_backoff_is_exponential_and_capped():
    # 指数增长:base * 2^attempt,封顶在 cap
    base, cap = 1.0, 30.0
    s0 = next_backoff_seconds(attempt=0, base=base, cap=cap, jitter=0.0)
    s1 = next_backoff_seconds(attempt=1, base=base, cap=cap, jitter=0.0)
    s2 = next_backoff_seconds(attempt=2, base=base, cap=cap, jitter=0.0)
    assert s0 == 1.0
    assert s1 == 2.0
    assert s2 == 4.0
    # 大 attempt 被 cap 封顶
    assert next_backoff_seconds(attempt=20, base=base, cap=cap, jitter=0.0) == cap


def test_backoff_jitter_stays_in_band():
    # jitter 加抖动但不超过 [base_value, base_value*(1+jitter)]
    val = next_backoff_seconds(attempt=3, base=1.0, cap=100.0, jitter=0.5)
    # attempt=3 → 8.0,jitter 0.5 → [8.0, 12.0]
    assert 8.0 <= val <= 12.0


def test_backoff_negative_attempt_rejected():
    with pytest.raises(ValueError):
        next_backoff_seconds(attempt=-1, base=1.0, cap=30.0, jitter=0.0)
