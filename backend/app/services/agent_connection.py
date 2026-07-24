"""Agent 连接管理器与消息协议(T4.1,设计 §5.2/§5.3/§15.5)。

控制面侧维护 agent_id → 活跃连接的映射,承载 §15.5 gRPC 双向流的控制面端职责:
- Agent 建流时 register、断流时 unregister;心跳刷新 last_seen。
- send_command 经该连接的 transport 下发 ServerCommand(§15.5)。
- 收到 Agent 上报(心跳/状态/结果 ACK)经 on_message 回调交给消费者(AgentGateway)。

传输无关(§5.1「统一模型对上,多态执行对下」):transport 是可注入的 send 端抽象,
真实实现是 gRPC stream,测试注入 fake。这样连接生命周期与消息路由的编排逻辑可
脱离网络单测;后续接真实 gRPC 时,上层(AgentGateway/业务)零改动。

线程模型:MVP 假设单事件循环内使用(FastAPI async),不加锁;若未来多 worker
共享需换分布式连接注册(如 Redis + 一致性哈希),届时替换本类实现即可。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AgentRoutingError(RuntimeError):
    """共享 Agent 连接路由不可用。"""


class AgentMessageKind(StrEnum):
    """Agent → 控制面的上报类型(§15.5 AgentMessage.payload)。"""

    HEARTBEAT = "heartbeat"  # 心跳:agent 版本、机器在线
    STATUS = "status"  # 服务状态上报
    RECEIVED = "received"  # 命令已收到 ACK(§5.4① 两段 ACK 第一段)
    RESULT = "result"  # 命令执行结果 ACK(第二段,推进 task 终态)


@dataclass(frozen=True)
class ServerCommand:
    """控制面 → Agent 的一条命令(§15.5 ServerCommand)。task_id 是幂等基石(§5.4)。"""

    task_id: str
    action: str  # exec | deploy | update_config | 生命周期动作名
    params: dict[str, str] = field(default_factory=dict)
    fence: int = 0  # fencing token(§5.4⑥),Agent 侧据此拒旧租约命令


@dataclass(frozen=True)
class AgentMessage:
    """Agent → 控制面的一条上报。result 类带 task_id + ok + detail 推进 task 状态机。"""

    agent_id: str
    kind: AgentMessageKind
    task_id: str | None = None
    ok: bool | None = None
    detail: str = ""


class CommandTransport(Protocol):
    """一个 agent 连接的命令下发端(§15.5 的 stream send 侧)。"""

    async def send(self, command: ServerCommand) -> None: ...


MessageHandler = Callable[[AgentMessage], None]


@dataclass
class _Connection:
    transport: CommandTransport
    last_seen: float


class AgentConnectionManager:
    """按 agent_id 维护活跃连接:登记/摘除、在线判定、命令下发、上报回调。"""

    def __init__(self, *, heartbeat_timeout: float = 30.0) -> None:
        self._conns: dict[str, _Connection] = {}
        self._handlers: list[MessageHandler] = []
        self._heartbeat_timeout = heartbeat_timeout

    def register(self, agent_id: str, transport: CommandTransport, *, now: float) -> None:
        """Agent 建流时登记连接,last_seen 置为当前时刻。"""
        self._conns[agent_id] = _Connection(transport=transport, last_seen=now)

    def unregister(self, agent_id: str) -> None:
        """Agent 断流时摘除连接。幂等:不存在也不报错。"""
        self._conns.pop(agent_id, None)

    def heartbeat(self, agent_id: str, *, now: float) -> None:
        """刷新某连接的 last_seen(收到心跳时调用)。连接不存在则忽略。"""
        conn = self._conns.get(agent_id)
        if conn is not None:
            conn.last_seen = now

    def is_online(self, agent_id: str, *, now: float) -> bool:
        """判断 agent 是否在线:有连接且距 last_seen 未超过心跳超时窗(§6.1 实时状态)。"""
        conn = self._conns.get(agent_id)
        if conn is None:
            return False
        return (now - conn.last_seen) <= self._heartbeat_timeout

    async def send_command(self, agent_id: str, command: ServerCommand) -> None:
        """向指定 agent 下发命令。无活跃连接抛 KeyError(调用方据此走离线分档 §5.4⑤)。"""
        conn = self._conns.get(agent_id)
        if conn is None:
            raise KeyError(f"agent 无活跃连接: {agent_id}")
        await conn.transport.send(command)

    def on_message(self, handler: MessageHandler) -> None:
        """注册上报消息回调(AgentGateway 用它把 result ACK 落到 task 状态机)。"""
        self._handlers.append(handler)

    async def start(self) -> None:
        """启动连接管理器。内存实现无需后台资源,供 Redis 实现统一生命周期。"""

    async def stop(self) -> None:
        """停止连接管理器。内存实现无需后台资源,供 Redis 实现统一生命周期。"""

    async def register_connection(
        self, agent_id: str, transport: CommandTransport, *, now: float
    ) -> None:
        """异步登记连接,保持内存与 Redis 实现使用同一 owner API。"""
        self.register(agent_id, transport, now=now)

    async def heartbeat_connection(self, agent_id: str, *, now: float) -> None:
        """异步刷新心跳,保持内存与 Redis 实现使用同一 owner API。"""
        self.heartbeat(agent_id, now=now)

    async def unregister_connection(self, agent_id: str) -> None:
        """异步摘除连接,保持内存与 Redis 实现使用同一 owner API。"""
        self.unregister(agent_id)

    async def handle_inbound(self, message: AgentMessage) -> None:
        """处理一条 Agent 上报:心跳刷新 last_seen,其余分发给已注册回调。"""
        if message.kind == AgentMessageKind.HEARTBEAT:
            # 心跳不需要 now 外部注入(上报即当下),但为保持 is_online 的可测确定性,
            # 心跳的 last_seen 刷新交由传输层在 handle 前调用 heartbeat(now=...);
            # 这里只把心跳也分发给回调(供上层记录 agent 版本等)。
            pass
        for handler in self._handlers:
            handler(message)
