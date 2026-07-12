"""Agent 连接管理器与消息协议验收(T4.1,设计 §5.2/§5.3/§15.5)。

控制面侧的连接管理器按 agent_id 维护活跃连接,支持:
- register/unregister:Agent 建流/断开时登记与摘除。
- is_online:查某 agent 是否有活跃连接(供离线分档决策 §5.4⑤)。
- send_command:向指定 agent 下发一条 ServerCommand(经该连接的可注入 transport)。
- 心跳更新 last_seen,支持按超时判定离线。
- 收到 CommandResult/received ACK 时,回调消费者(供 AgentGateway 推进 task)。

传输无关(§5.1):真实 gRPC 双向流后续接入,这里用可注入的 fake transport
验证连接生命周期与消息路由的编排逻辑,与既有 SSHExecutor/pipeline 的可注入
connector/http_client 一致。用冻结时钟,不触真实网络。
"""

from __future__ import annotations

import pytest

from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    ServerCommand,
)


class FakeTransport:
    """记录下发命令的假连接传输。真实实现是 gRPC stream 的 send 端。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[ServerCommand] = []

    async def send(self, command: ServerCommand) -> None:
        if self.fail:
            raise RuntimeError("stream broken")
        self.sent.append(command)


async def test_register_and_online():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=100.0)

    assert mgr.is_online("agent-1", now=100.0) is True
    assert mgr.is_online("agent-2", now=100.0) is False


async def test_unregister_marks_offline():
    mgr = AgentConnectionManager()
    mgr.register("agent-1", FakeTransport(), now=100.0)
    mgr.unregister("agent-1")
    assert mgr.is_online("agent-1", now=100.0) is False


async def test_heartbeat_keeps_online_within_timeout():
    mgr = AgentConnectionManager(heartbeat_timeout=30.0)
    mgr.register("agent-1", FakeTransport(), now=100.0)
    # 心跳刷新 last_seen
    mgr.heartbeat("agent-1", now=120.0)
    assert mgr.is_online("agent-1", now=145.0) is True  # 145-120=25 < 30
    # 超过超时窗未见心跳 → 离线
    assert mgr.is_online("agent-1", now=160.0) is False  # 160-120=40 > 30


async def test_send_command_dispatches_through_transport():
    mgr = AgentConnectionManager()
    transport = FakeTransport()
    mgr.register("agent-1", transport, now=100.0)

    cmd = ServerCommand(task_id="t1", action="restart", params={"unit": "billing.service"})
    await mgr.send_command("agent-1", cmd)

    assert transport.sent == [cmd]


async def test_send_command_to_offline_agent_raises():
    mgr = AgentConnectionManager()
    cmd = ServerCommand(task_id="t1", action="restart", params={})
    with pytest.raises(KeyError):
        await mgr.send_command("nope", cmd)


async def test_inbound_message_dispatched_to_handler():
    mgr = AgentConnectionManager()
    received: list[AgentMessage] = []
    mgr.on_message(lambda m: received.append(m))

    msg = AgentMessage(
        agent_id="agent-1",
        kind=AgentMessageKind.RESULT,
        task_id="t1",
        ok=True,
        detail="done",
    )
    await mgr.handle_inbound(msg)

    assert received == [msg]
