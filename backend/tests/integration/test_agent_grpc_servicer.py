"""Agent gRPC servicer 桥接验收(T4.1 wire,设计 §15.5/§5.4)。

把 §15.5 的 Connect 双向流桥接到 AgentConnectionManager:上行 AgentMessage 转成
dataclass 交给 manager(心跳刷 last_seen、ack 唤醒等待者),下行经 asyncio.Queue
transport 把 ServerCommand yield 回 agent 流。

用内存 servicer + fake 上行流,不起真实 gRPC socket——桥接编排逻辑可脱网单测。
覆盖:
- Agent 建流(首条带 agent_id)→ manager 登记连接、置在线。
- 心跳上行 → manager 刷新 last_seen。
- 控制面下发命令 → 经 transport 队列 yield 到 agent 流(带 task_id/fence)。
- result ACK 上行 → manager 分发给回调(唤醒 AgentGateway 等待者)。
- 流结束(agent 断开)→ manager 摘除连接、置离线。
"""

from __future__ import annotations

import asyncio

import pytest

from app.grpc_gen import agent_pb2
from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    ServerCommand,
)
from app.services.agent_grpc import AgentServicer


class _FakeContext:
    """最小 grpc.aio.ServicerContext 替身:仅承载对端信息,测试不校验。"""

    def peer(self) -> str:
        return "ipv4:10.0.0.9:5000"


async def _drain_commands(response_iter, sink: list, *, stop: asyncio.Event) -> None:
    """把 servicer 下行的 ServerCommand 收进 sink,直到 stop 置位。"""
    async for command in response_iter:
        sink.append(command)
        if stop.is_set():
            return


async def test_connect_registers_and_unregisters():
    mgr = AgentConnectionManager(heartbeat_timeout=100.0)
    servicer = AgentServicer(mgr, clock=lambda: 0.0)

    async def request_iter():
        # 首条:带 agent_id 的心跳(建流即登记)
        yield agent_pb2.AgentMessage(
            agent_id="agent-1",
            heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0"),
        )
        # 流到此结束(模拟 agent 断开)

    commands: list = []
    response_iter = servicer.Connect(request_iter(), _FakeContext())
    # 消费下行流直到耗尽(request 流结束后 servicer 应收尾)
    async for cmd in response_iter:
        commands.append(cmd)

    # 流结束后连接被摘除 → 离线
    assert mgr.is_online("agent-1", now=0.0) is False


async def test_heartbeat_keeps_online_and_command_dispatched():
    mgr = AgentConnectionManager(heartbeat_timeout=100.0)
    servicer = AgentServicer(mgr, clock=lambda: 0.0)

    inbound = asyncio.Queue()
    stop = asyncio.Event()

    async def request_iter():
        # 建流心跳
        yield agent_pb2.AgentMessage(
            agent_id="agent-1", heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0")
        )
        # 挂住,等测试主动结束(保持流存活以便下发命令)
        await stop.wait()

    received: list = []

    async def consume():
        async for command in servicer.Connect(request_iter(), _FakeContext()):
            received.append(command)

    task = asyncio.create_task(consume())
    # 等 servicer 登记连接
    for _ in range(200):
        if mgr.is_online("agent-1", now=0.0):
            break
        await asyncio.sleep(0.005)
    assert mgr.is_online("agent-1", now=0.0)

    # 控制面下发命令 → 应经 transport yield 到 agent 流
    await mgr.send_command(
        "agent-1", ServerCommand(task_id="t-1", action="exec", params={"cmd": "uptime"}, fence=3)
    )
    for _ in range(200):
        if received:
            break
        await asyncio.sleep(0.005)
    assert received, "命令应下发到 agent 流"
    assert received[0].task_id == "t-1"
    assert received[0].action == "exec"
    assert received[0].fence == 3
    assert received[0].params["cmd"] == "uptime"

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_result_ack_dispatched_to_handler():
    mgr = AgentConnectionManager(heartbeat_timeout=100.0)
    servicer = AgentServicer(mgr, clock=lambda: 0.0)

    got: list[AgentMessage] = []
    mgr.on_message(got.append)

    stop = asyncio.Event()

    async def request_iter():
        yield agent_pb2.AgentMessage(
            agent_id="agent-1", heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0")
        )
        # result ACK 上行(第二段 ACK,推进 task 终态)
        yield agent_pb2.AgentMessage(
            agent_id="agent-1",
            ack=agent_pb2.CommandAck(
                task_id="t-9", kind=agent_pb2.ACK_KIND_RESULT, ok=True, detail="done"
            ),
        )
        await stop.wait()

    async def consume():
        async for _ in servicer.Connect(request_iter(), _FakeContext()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(200):
        if any(m.kind == AgentMessageKind.RESULT for m in got):
            break
        await asyncio.sleep(0.005)

    results = [m for m in got if m.kind == AgentMessageKind.RESULT]
    assert results, "result ACK 应分发给回调"
    assert results[0].task_id == "t-9"
    assert results[0].ok is True
    assert results[0].detail == "done"

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_first_message_without_agent_id_aborts():
    mgr = AgentConnectionManager()
    servicer = AgentServicer(mgr, clock=lambda: 0.0)

    async def request_iter():
        # 首条无 agent_id → 协议违规,servicer 应拒绝(不登记任何连接)
        yield agent_pb2.AgentMessage(heartbeat=agent_pb2.Heartbeat(agent_version="x"))

    with pytest.raises(Exception):  # noqa: B017 - 具体异常类型由实现决定,这里只验证拒绝
        async for _ in servicer.Connect(request_iter(), _FakeContext()):
            pass
