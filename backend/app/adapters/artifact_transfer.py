"""经 SSH/SFTP 向目标服务器传输制品(artifact 直接部署 Task 2)。

ArtifactTransfer 是窄契约：只负责把单个本地文件上传到指定远端路径，
不依赖 Executor ABC，不承担部署命令执行，不扩大 SSHExecutor 的职责边界。

设计取舍：
- Protocol 接口保持最小（仅 upload），便于单测注入 fake。
- SshArtifactTransfer 内部连接模式与 SSHExecutor 完全一致（同一 SSHTarget +
  connector 注入模式），复用 auth_type key/password 分支，机密不落属性。
- 上传前先校验本地文件存在，确保在任何网络动作发生前拒绝明显错误。
- 连接和 SFTP 层的所有异常统一翻译为 AppError artifact_upload_failed(502)，
  与生命周期动作的 ssh_error 失败语义对齐，让上层无需区分传输层细节。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.adapters.ssh_executor import SSHTarget
from app.core.errors import AppError
from app.core.secrets import SecretStore

# 连接工厂类型：与 SSHExecutor 共用相同模式（返回 async context manager）。
Connector = Callable[..., Any]


class ArtifactTransfer(Protocol):
    """制品传输窄接口：仅上传单个本地文件到远端路径。"""

    async def upload(self, local_path: str, remote_path: str) -> None: ...


class AgentArtifactTransport(Protocol):
    async def upload_artifact(self, local_path: str, remote_path: str) -> None: ...


class AgentArtifactTransfer:
    """通过已认证 Agent 流传输制品，不绕过 ArtifactTransfer 契约。"""

    def __init__(self, gateway: AgentArtifactTransport) -> None:
        self._gateway = gateway

    async def upload(self, local_path: str, remote_path: str) -> None:
        await self._gateway.upload_artifact(local_path, remote_path)


def _default_connector(**kwargs: Any) -> Any:
    """默认连接工厂（与 SSHExecutor 共用 asyncssh.connect 语义）。"""
    import asyncssh

    return asyncssh.connect(**kwargs)


class SshArtifactTransfer:
    """经 SSH/SFTP 把本地制品上传到目标服务器。

    与 SSHExecutor 共用 SSHTarget 和 connector 注入模式，机密每次建连时从
    保险箱取用，不缓存明文（§13 凭证保险箱）。
    """

    def __init__(
        self,
        target: SSHTarget,
        secrets: SecretStore,
        *,
        connector: Connector | None = None,
    ) -> None:
        self._target = target
        self._secrets = secrets
        self._connector = connector or _default_connector

    def _connect(self) -> Any:
        """构造 SSH 连接上下文（每次调用时从保险箱取机密，不缓存明文）。"""
        secret = self._secrets.get(self._target.credential_id)
        kwargs: dict[str, Any] = {
            "host": self._target.host,
            "port": self._target.port,
            "username": self._target.username,
            "connect_timeout": self._target.connect_timeout,
            "known_hosts": None,
        }
        if self._target.auth_type == "password":
            kwargs["password"] = secret
        else:
            kwargs["client_key"] = secret
        return self._connector(**kwargs)

    async def upload(self, local_path: str, remote_path: str) -> None:
        """把 local_path 上传到目标服务器的 remote_path。

        上传前验证本地文件存在；远端父目录幂等建立；所有传输层异常
        翻译为统一的 502。
        """
        if not Path(local_path).is_file():
            raise AppError(
                "artifact_local_not_found",
                f"本地制品文件不存在: {local_path}",
                status_code=404,
            )

        remote_parent = str(PurePosixPath(remote_path).parent)
        try:
            async with self._connect() as conn:
                async with conn.start_sftp_client() as sftp:
                    await sftp.makedirs(remote_parent, exist_ok=True)
                    await sftp.put(local_path, remote_path)
        except AppError:
            raise  # 已经是 AppError（如 credential 取不到），直接上抛
        except Exception as exc:
            raise AppError(
                "artifact_upload_failed",
                f"制品 SFTP 上传失败: {exc}",
                status_code=502,
            ) from exc
