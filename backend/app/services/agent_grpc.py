"""Agent gRPC servicer:把 §15.5 的 Connect 双向流桥接到 AgentConnectionManager。

真实 wire 层(T4.1)。控制面侧 gRPC server 的业务实现:Agent 主动外连建流后,
- **上行**(Agent→控制面):AgentMessage(心跳/状态/结果 ACK)转成 dataclass 交给
  manager——心跳刷 last_seen 并置在线,ack 分发给回调(唤醒 AgentGateway 等待者)。
- **下行**(控制面→Agent):manager.send_command 经本 servicer 注入的 Queue transport
  入队,Connect 的响应生成器出队并把 dataclass 转回 protobuf yield 给 agent 流。

设计要点:
- **传输无关落地**:AgentConnectionManager 只认 CommandTransport.send(dataclass),
  本类用 asyncio.Queue 把「下发」与「gRPC 流的 yield」解耦——业务侧下发不阻塞在
  网络写上,流侧按序取出。真实 gRPC 与单测桥接逻辑一致(§5.1)。
- **首条必带 agent_id**:建流第一条消息用于登记连接;缺 agent_id 视为协议违规,
  abort(测试用普通异常验证拒绝)。
- **流结束即摘除**:agent 断开(request 流耗尽或异常)→ finally 里 unregister,
  置离线,供 §5.4 离线分档与 fencing 判定。
- clock 可注入,保证 is_online 判定在测试内确定。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from time import monotonic
from typing import Any

from app.core.logging import get_logger
from app.grpc_gen import agent_pb2, agent_pb2_grpc
from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    ServerCommand,
)

log = get_logger("agent_grpc")


class _QueueTransport:
    """把 manager 的下发(dataclass)入队,供 Connect 响应流出队转 protobuf yield。

    manager.send_command 只 await 一次入队(不阻塞在网络写);流侧独立消费。
    队列在流结束时由 servicer 投毒(None)唤醒收尾。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ServerCommand | None] = asyncio.Queue()

    async def send(self, command: ServerCommand) -> None:
        await self._queue.put(command)

    def close(self) -> None:
        # 同步投毒丸:供 pump_inbound 完成回调调用,唤醒下行 commands() 收尾。
        self._queue.put_nowait(None)

    async def commands(self) -> AsyncIterator[ServerCommand]:
        while True:
            item = await self._queue.get()
            if item is None:  # 收尾毒丸
                return
            yield item


def _to_agent_message(msg: Any) -> AgentMessage:
    """protobuf AgentMessage → 内部 dataclass。按 oneof 分派到心跳/状态/ACK。"""
    which = msg.WhichOneof("payload")
    if which == "heartbeat":
        return AgentMessage(agent_id=msg.agent_id, kind=AgentMessageKind.HEARTBEAT)
    if which == "status":
        return AgentMessage(
            agent_id=msg.agent_id,
            kind=AgentMessageKind.STATUS,
            detail=msg.status.detail,
        )
    if which == "ack":
        ack = msg.ack
        kind = (
            AgentMessageKind.RESULT
            if ack.kind == agent_pb2.ACK_KIND_RESULT
            else AgentMessageKind.RECEIVED
        )
        return AgentMessage(
            agent_id=msg.agent_id,
            kind=kind,
            task_id=ack.task_id or None,
            ok=ack.ok,
            detail=ack.detail,
        )
    # 未知 payload:当作心跳保活,不中断流
    return AgentMessage(agent_id=msg.agent_id, kind=AgentMessageKind.HEARTBEAT)


def _to_pb_command(command: ServerCommand) -> Any:
    """内部 ServerCommand dataclass → protobuf ServerCommand。"""
    pb = agent_pb2.ServerCommand(
        task_id=command.task_id, action=command.action, fence=command.fence
    )
    for key, value in command.params.items():
        pb.params[key] = value
    return pb


class AgentServicer(agent_pb2_grpc.AgentServiceServicer):
    """AgentService.Connect 的控制面实现,桥接到 AgentConnectionManager。"""

    def __init__(
        self,
        manager: AgentConnectionManager,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._manager = manager
        self._clock = clock

    async def Connect(  # noqa: N802 - gRPC 生成的方法名,须一致
        self, request_iterator: AsyncIterator[Any], context: Any
    ) -> AsyncIterator[Any]:
        """双向流:登记连接 → 并发处理上行 + 下发下行 → 流结束摘除连接。"""
        transport = _QueueTransport()
        agent_id: str | None = None

        async def pump_inbound() -> None:
            """消费上行流:首条登记连接,心跳刷 last_seen,其余交 manager 分发。"""
            nonlocal agent_id
            async for pb_msg in request_iterator:
                if agent_id is None:
                    agent_id = pb_msg.agent_id
                    if not agent_id:
                        raise ValueError("首条 AgentMessage 缺少 agent_id,拒绝建流")
                    self._manager.register(agent_id, transport, now=self._clock())
                message = _to_agent_message(pb_msg)
                if message.kind == AgentMessageKind.HEARTBEAT:
                    self._manager.heartbeat(agent_id, now=self._clock())
                await self._manager.handle_inbound(message)

        inbound_task = asyncio.create_task(pump_inbound())
        # pump 结束(agent 断流或异常)即投毒丸,唤醒下行 commands() 收尾,避免流悬挂。
        inbound_task.add_done_callback(lambda _t: transport.close())
        try:
            # 等首条消息登记连接后再吐命令;若 pump 立即失败(无 agent_id),抛出
            while agent_id is None and not inbound_task.done():
                await asyncio.sleep(0.001)
            if inbound_task.done():
                # 传播 pump 的异常(如缺 agent_id),或正常空流结束
                inbound_task.result()
                return
            # 下行:把 transport 队列里的命令转 protobuf yield 给 agent
            async for command in transport.commands():
                yield _to_pb_command(command)
        finally:
            inbound_task.cancel()
            try:
                await inbound_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾吞异常
                pass
            if agent_id is not None:
                self._manager.unregister(agent_id)
