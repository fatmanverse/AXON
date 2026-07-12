"""Agent gRPC 端到端验收(T4.1 wire,设计 §15.5/§5.4)。

起真实 grpc.aio server(端口 0 由 OS 分配)+ 真实 grpc.aio 客户端(充当 Agent),
验证整条 wire 打通:
- Agent 外连建双向流、发心跳 → 控制面置该 agent 在线。
- 控制面经 AgentGateway 下发命令 → 命令经 gRPC 流到达 Agent。
- Agent 回 result ACK → AgentGateway 的 exec 返回成功。

这是不打桩的真实网络往返,证明 servicer + server + manager + gateway 协同无误。
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from app.adapters.agent_gateway import AgentGateway
from app.grpc_gen import agent_pb2, agent_pb2_grpc
from app.services.agent_connection import AgentConnectionManager
from app.services.agent_grpc_server import AgentGrpcServer


async def _wait(predicate, *, timeout: float = 2.0) -> None:
    """轮询等待条件成立,超时抛。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


async def test_end_to_end_command_roundtrip():
    manager = AgentConnectionManager(heartbeat_timeout=100.0)
    server = AgentGrpcServer(manager, port=0)
    await server.start()
    port = server.bound_port
    assert port

    outbound: asyncio.Queue = asyncio.Queue()  # 测试驱动 agent 上行的消息
    agent_done = asyncio.Event()

    async def agent_upstream():
        # 首条心跳建流(带 agent_id)
        yield agent_pb2.AgentMessage(
            agent_id="agent-e2e",
            heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0"),
        )
        # 之后按测试指令上行(如 result ACK),直到收尾
        while True:
            msg = await outbound.get()
            if msg is None:
                return
            yield msg

    received_commands: list = []

    async def run_agent():
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            call = stub.Connect(agent_upstream())
            async for command in call:
                received_commands.append(command)
                # 收到命令 → 回 result ACK(模拟执行成功)
                await outbound.put(
                    agent_pb2.AgentMessage(
                        agent_id="agent-e2e",
                        ack=agent_pb2.CommandAck(
                            task_id=command.task_id,
                            kind=agent_pb2.ACK_KIND_RESULT,
                            ok=True,
                            detail="executed",
                        ),
                    )
                )
        agent_done.set()

    agent_task = asyncio.create_task(run_agent())
    try:
        # 等 agent 上线
        await _wait(lambda: manager.is_online("agent-e2e", now=manager_now()))

        # 控制面经 AgentGateway 下发命令,等 result ACK
        gateway = AgentGateway(
            manager=manager, agent_id="agent-e2e", ack_timeout=3.0, fence=1
        )
        result = await gateway.exec("systemctl restart billing")

        assert result.succeeded
        assert result.stdout == "executed"
        assert received_commands
        assert received_commands[0].action == "exec"
        assert received_commands[0].params["command"] == "systemctl restart billing"
    finally:
        await outbound.put(None)  # 让 agent 上行流收尾
        await server.stop()
        agent_task.cancel()
        try:
            await agent_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def manager_now() -> float:
    # 与 AgentGrpcServer 默认 clock(monotonic)一致
    from time import monotonic

    return monotonic()


async def test_server_start_stop_idempotent():
    manager = AgentConnectionManager()
    server = AgentGrpcServer(manager, port=0)
    await server.start()
    await server.start()  # 幂等:第二次不报错
    assert server.bound_port
    await server.stop()
    await server.stop()  # 幂等
