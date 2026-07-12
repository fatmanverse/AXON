"""AgentGateway 真实现验收(T4.3,设计 §5.1/§5.3/§5.4)。

把占位 AgentGateway 替换为经 AgentConnectionManager 下发命令、等 result ACK 的
真实现,同时保持统一 Executor 接口不变(§5.1「统一模型对上,多态执行对下」——
上层业务与 UI 零改动)。

覆盖:
- exec/update_config:经连接管理器下发 ServerCommand,收到 result ACK(ok=True)
  返回成功 CommandResult;ok=False 返回失败(非 0 退出码 + detail 入 stderr)。
- Agent 离线(无连接):下发抛 KeyError → 归一为 AppError,上层据此走离线分档。
- 超时未收到 result ACK:抛超时 AppError(对应 §5.4④ 上层据此判 unknown)。
- 下发的命令带正确的 task_id 与 fence(§5.4⑥ 幂等基石)。

用真实 AgentConnectionManager + fake transport,不触真实 gRPC;超时用极小值提速。
"""

from __future__ import annotations

import asyncio

import pytest

from app.adapters.agent_gateway import AgentGateway
from app.core.errors import AppError
from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    ServerCommand,
)


class FakeTransport:
    """记录下发命令的假 transport。"""

    def __init__(self) -> None:
        self.sent: list[ServerCommand] = []

    async def send(self, command: ServerCommand) -> None:
        self.sent.append(command)


def _gateway(mgr: AgentConnectionManager, agent_id: str = "agent-1") -> AgentGateway:
    return AgentGateway(manager=mgr, agent_id=agent_id, ack_timeout=1.0, fence=7)


async def _ack_after(mgr, transport, *, ok: bool, detail: str = ""):
    """等命令下发后,用其 task_id 回一条 result ACK(模拟 Agent 执行完回传)。"""
    while not transport.sent:
        await asyncio.sleep(0.001)
    task_id = transport.sent[-1].task_id
    await mgr.handle_inbound(
        AgentMessage(
            agent_id="agent-1",
            kind=AgentMessageKind.RESULT,
            task_id=task_id,
            ok=ok,
            detail=detail,
        )
    )


async def test_exec_success_on_result_ack():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=0.0)
    gw = _gateway(mgr)

    result, _ = await asyncio.gather(
        gw.exec("systemctl restart billing"),
        _ack_after(mgr, transport, ok=True, detail="done"),
    )
    assert result.succeeded
    assert result.exit_code == 0


async def test_exec_failure_on_negative_ack():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=0.0)
    gw = _gateway(mgr)

    result, _ = await asyncio.gather(
        gw.exec("bad cmd"),
        _ack_after(mgr, transport, ok=False, detail="exit 1"),
    )
    assert not result.succeeded
    assert result.exit_code != 0
    assert "exit 1" in result.stderr


async def test_command_carries_task_id_and_fence():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=0.0)
    gw = _gateway(mgr)

    await asyncio.gather(
        gw.exec("uptime"),
        _ack_after(mgr, transport, ok=True),
    )
    sent = transport.sent[-1]
    assert sent.task_id  # 非空,幂等基石
    assert sent.fence == 7


async def test_offline_agent_raises_apperror():
    mgr = AgentConnectionManager()  # 未 register,agent 离线
    gw = _gateway(mgr)
    with pytest.raises(AppError) as exc:
        await gw.exec("uptime")
    # 无连接归一为明确错误,供上层走离线分档(§5.4⑤)
    assert exc.value.status_code in (409, 502, 503)


async def test_timeout_without_ack_raises():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=0.0)
    # ack_timeout 极小且不回 ACK → 超时
    gw = AgentGateway(manager=mgr, agent_id="agent-1", ack_timeout=0.05, fence=1)
    with pytest.raises(AppError) as exc:
        await gw.exec("hang")
    assert exc.value.status_code == 504


async def test_update_config_success_on_result_ack():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=0.0)
    gw = _gateway(mgr)

    result, _ = await asyncio.gather(
        gw.update_config("/etc/app.env", "A=1"),
        _ack_after(mgr, transport, ok=True),
    )
    assert result.succeeded
    # 下发的是 update_config 动作
    assert transport.sent[-1].action == "update_config"
