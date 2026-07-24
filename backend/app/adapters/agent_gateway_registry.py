"""AgentGateway 注册表(T4.3 生产接线)。

问题背景:AgentGateway 真实形态在 __init__ 里 `manager.on_message(self._on_message)`
注册一个结果 ACK 回调。若每次动作都新建一个 gateway,回调会永久累积在 manager 的
handler 列表上(内存泄漏 + 旧 gateway 的 _pending 永不回收)。

因此真实形态的 gateway 必须**每个 agent 一个、全程复用**:同一 agent 的并发命令靠
gateway 内 task_id → future 的 _pending 字典天然隔离(task_id 每次现生成,不冲突)。
本注册表按 agent_id 缓存 gateway,与共享的 AgentConnectionManager 绑定,在启动时
构造一次(见 main.py lifespan),供执行器工厂按 server.agent_id 取用。
"""

from __future__ import annotations

from app.adapters.agent_gateway import AgentGateway
from app.services.agent_connection import AgentConnectionManager


class AgentGatewayRegistry:
    """按 agent_id 复用 AgentGateway,避免每次动作重复注册 manager 回调。"""

    def __init__(
        self,
        manager: AgentConnectionManager,
        *,
        ack_timeout: float = 30.0,
        artifact_chunk_bytes: int = 192 * 1024,
        artifact_max_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self._manager = manager
        self._ack_timeout = ack_timeout
        self._artifact_chunk_bytes = artifact_chunk_bytes
        self._artifact_max_bytes = artifact_max_bytes
        self._gateways: dict[str, AgentGateway] = {}

    def for_agent(self, agent_id: str) -> AgentGateway:
        """取(或懒建)该 agent 的复用 gateway。回调仅在首次构造时注册一次。"""
        gateway = self._gateways.get(agent_id)
        if gateway is None:
            gateway = AgentGateway(
                manager=self._manager,
                agent_id=agent_id,
                ack_timeout=self._ack_timeout,
                artifact_chunk_bytes=self._artifact_chunk_bytes,
                artifact_max_bytes=self._artifact_max_bytes,
            )
            self._gateways[agent_id] = gateway
        return gateway
