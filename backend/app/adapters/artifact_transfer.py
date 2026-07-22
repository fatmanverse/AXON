"""本地制品经 SSH/SFTP 上传到远端的窄适配器。"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.adapters.ssh_executor import SSHTarget, build_ssh_connect_kwargs
from app.core.errors import AppError
from app.core.secrets import SecretStore


class ArtifactTransfer(Protocol):
    async def upload(self, local_path: str, remote_path: str) -> None: ...


def _default_connector(**kwargs: Any) -> Any:
    import asyncssh

    return asyncssh.connect(**kwargs)


class SshArtifactTransfer:
    def __init__(
        self,
        target: SSHTarget,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._target = target
        self._secrets = secrets
        self._connector = connector or _default_connector

    async def upload(self, local_path: str, remote_path: str) -> None:
        path = Path(local_path)
        if path.is_symlink() or not path.is_file():
            raise AppError(
                "artifact_file_not_found",
                "本地制品文件不存在",
                status_code=404,
            )

        try:
            connect_kwargs = build_ssh_connect_kwargs(self._target, self._secrets)
            async with self._connector(**connect_kwargs) as connection:
                async with connection.start_sftp_client() as sftp:
                    parent = posixpath.dirname(remote_path)
                    if parent:
                        await sftp.makedirs(parent, exist_ok=True)
                    await sftp.put(local_path, remote_path)
        except Exception as exc:
            raise AppError(
                "artifact_upload_failed",
                "制品上传失败",
                status_code=502,
            ) from exc
