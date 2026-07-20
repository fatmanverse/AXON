"""按 server 构造统一 Executor 的共享工厂(§5.1)。

生命周期动作(LifecycleService)与配置下发(ConfigDeliveryService)都需要「给一台
server 造一个 Executor」。把这段 SSHTarget 组装逻辑集中一处,避免两边各维护一份
易漂移的实现:SSH 走 SSHExecutor(机密经 credential_id 从保险箱取),agent 或无
server 走 AgentGateway 占位(§5.3)。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.agent_gateway import AgentGateway
from app.adapters.agent_gateway_registry import AgentGatewayRegistry
from app.adapters.artifact_transfer import ArtifactTransfer, SshArtifactTransfer
from app.adapters.executor import Executor
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.errors import AppError
from app.core.secrets import SecretStore
from app.models.server import AccessMode, Server


def build_ssh_target_for_server(server: Server) -> SSHTarget:
    labels = server.labels or {}
    return SSHTarget(
        host=server.host,
        port=int(labels.get("ssh_port", 22)),
        username=str(labels.get("ssh_username", "root")),
        credential_id=server.ssh_credential_id or "",
        auth_type=str(labels.get("auth_type", "key")),
    )


def build_executor_for_server(
    server: Server | None,
    secrets: SecretStore,
    *,
    connector: Callable[..., Any] | None = None,
    agent_registry: AgentGatewayRegistry | None = None,
) -> Executor:
    """按 server.access_mode 选择执行器。

    agent 模式:注入了 agent_registry 且 server.agent_id 存在时,返回该 agent 复用的
    真实 AgentGateway(经连接管理器下发命令,§5.3/§5.4);否则退回占位形态(抛未接入
    错误,不影响 SSH 路径)。ssh 模式从 labels 取端口/用户/认证方式,机密靠
    credential_id 引用保险箱。无 server 视为占位。
    """
    if server is None or server.access_mode == AccessMode.AGENT:
        if server is not None and agent_registry is not None and server.agent_id:
            return agent_registry.for_agent(server.agent_id)
        return AgentGateway()

    target = build_ssh_target_for_server(server)
    return SSHExecutor(target, secrets, connector=connector)


def build_artifact_transfer_for_server(
    server: Server | None,
    secrets: SecretStore,
    *,
    connector: Callable[..., Any] | None = None,
) -> ArtifactTransfer:
    """按 server.access_mode 构造制品传输器,目前仅 SSH 支持 SFTP。"""
    if server is None or server.access_mode == AccessMode.AGENT:
        raise AppError(
            "artifact_transfer_not_supported",
            "当前服务器接入模式不支持制品上传",
            status_code=501,
        )
    return SshArtifactTransfer(
        build_ssh_target_for_server(server),
        secrets,
        connector=connector,
    )
