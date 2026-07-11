"""AgentGateway 占位实现(T1.5,设计 §5.3)。

MVP 阶段用 SSH 起步,Agent 通道仅预留接口。本占位实现统一 Executor 接口,
所有动作抛 AgentNotConnectedError —— 保证 access_mode=agent 的服务器操作
返回明确的"未接入 Agent"提示,而非 500 或静默失败,且不影响 SSH 路径。

后续 T4.3 用真实 gRPC 双向流实现替换本占位,上层业务与 UI 零改动
(§5.1「统一模型对上,多态执行对下」)。
"""

from __future__ import annotations

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.errors import AppError


class AgentNotConnectedError(AppError):
    """Agent 通道尚未接入。

    用 501 Not Implemented 而非 4xx/500:语义上是"此能力暂未实现",
    区别于客户端错误(4xx)与服务端故障(500),便于前端针对性提示
    "该服务器为 Agent 模式,Agent 功能尚未上线"。
    """

    def __init__(self) -> None:
        super().__init__(
            "agent_not_connected",
            "该服务器为 Agent 接入模式,Agent 通道尚未接入,暂不支持此操作",
            status_code=501,
        )


class AgentGateway(Executor):
    """Agent 执行器占位:实现接口但拒绝所有动作。"""

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        raise AgentNotConnectedError()

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        raise AgentNotConnectedError()

    async def update_config(self, path: str, content: str) -> CommandResult:
        raise AgentNotConnectedError()

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        raise AgentNotConnectedError()
