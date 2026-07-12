"""按 server 构造统一 Executor 的共享工厂(§5.1)。

生命周期动作(LifecycleService)与配置下发(ConfigDeliveryService)都需要「给一台
server 造一个 Executor」。把这段 SSHTarget 组装逻辑集中一处,避免两边各维护一份
易漂移的实现:SSH 走 SSHExecutor(私钥经 credential_id 从保险箱取),agent 或无
server 走 AgentGateway 占位(§5.3)。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.agent_gateway import AgentGateway
from app.adapters.executor import Executor
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.secrets import SecretStore
from app.models.server import AccessMode, Server


def build_executor_for_server(
    server: Server | None,
    secrets: SecretStore,
    *,
    connector: Callable[..., Any] | None = None,
) -> Executor:
    """按 server.access_mode 选择执行器。

    无 server 或 agent 模式暂走 AgentGateway 占位(当前抛未接入错误,不影响 SSH
    路径,§5.3);ssh 模式从 labels 取端口/用户,私钥靠 credential_id 引用保险箱。
    """
    if server is None or server.access_mode == AccessMode.AGENT:
        return AgentGateway()

    labels = server.labels or {}
    target = SSHTarget(
        host=server.host,
        port=int(labels.get("ssh_port", 22)),
        username=str(labels.get("ssh_username", "root")),
        credential_id=server.ssh_credential_id or "",
    )
    return SSHExecutor(target, secrets, connector=connector)
