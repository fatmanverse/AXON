"""SshArtifactTransfer 单元测试(artifact 直接部署 Task 2)。

用 fake connector 隔离真实 SSH 网络，验证：
- 成功路径：makedirs + put 按顺序调用
- 本地文件不存在：抛 404 AppError，无网络动作
- 连接失败：翻译为 AppError artifact_upload_failed 502
- SFTP 异常：翻译为 AppError artifact_upload_failed 502
- 认证参数：密钥 / 密码按 auth_type 分别传
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.adapters.artifact_transfer import SshArtifactTransfer
from app.adapters.ssh_executor import SSHTarget
from app.core.errors import AppError

# ── fake connector helpers ─────────────────────────────────────────────────


class FakeSftp:
    """记录 makedirs / put 调用的 fake SFTP client。"""

    def __init__(self) -> None:
        self.makedirs_calls: list[tuple[str, bool]] = []
        self.put_calls: list[tuple[str, str]] = []
        self._raise_on_put: Exception | None = None

    def set_put_error(self, exc: Exception) -> None:
        self._raise_on_put = exc

    async def makedirs(self, path: str, *, exist_ok: bool = False) -> None:
        self.makedirs_calls.append((path, exist_ok))

    async def put(self, local_path: str, remote_path: str) -> None:
        if self._raise_on_put is not None:
            raise self._raise_on_put
        self.put_calls.append((local_path, remote_path))

    async def __aenter__(self) -> FakeSftp:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConn:
    """记录 SFTP 上下文的 fake SSH connection。"""

    def __init__(self, sftp: FakeSftp, *, fail_connect: bool = False) -> None:
        self._sftp = sftp
        self._fail_connect = fail_connect

    def start_sftp_client(self) -> FakeSftp:
        return self._sftp

    async def __aenter__(self) -> FakeConn:
        if self._fail_connect:
            raise OSError("connection refused")
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


def _make_connector(conn: FakeConn):
    def connector(**_kwargs: Any) -> FakeConn:
        return conn

    return connector


def _make_transfer(
    auth_type: str = "key",
    *,
    connector=None,
    credential: str = "fake-cred",
) -> tuple[SshArtifactTransfer, FakeSftp]:
    sftp = FakeSftp()
    conn = FakeConn(sftp)
    target = SSHTarget(
        host="10.0.0.1",
        port=22,
        username="deploy",
        credential_id="cred-1",
        auth_type=auth_type,
    )
    secrets = MagicMock()
    secrets.get.return_value = credential

    xfer = SshArtifactTransfer(
        target,
        secrets,
        connector=connector or _make_connector(conn),
    )
    return xfer, sftp


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_success(tmp_path: Path):
    """成功路径：makedirs 建父目录，put 上传文件。"""
    local = tmp_path / "app.tar.gz"
    local.write_bytes(b"data")

    xfer, sftp = _make_transfer()
    await xfer.upload(str(local), "/tmp/axon-artifacts/abc.tar.gz")

    assert sftp.makedirs_calls == [("/tmp/axon-artifacts", True)]
    assert sftp.put_calls == [(str(local), "/tmp/axon-artifacts/abc.tar.gz")]


@pytest.mark.asyncio
async def test_upload_local_not_found():
    """本地文件不存在：抛 404，不建连接。"""
    xfer, sftp = _make_transfer()
    with pytest.raises(AppError) as exc_info:
        await xfer.upload("/nonexistent/app.tar.gz", "/tmp/axon-artifacts/x.tar.gz")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "artifact_local_not_found"
    # 没有发起网络动作
    assert sftp.makedirs_calls == []
    assert sftp.put_calls == []


@pytest.mark.asyncio
async def test_upload_connection_failure(tmp_path: Path):
    """SSH 连接失败：翻译为 502 artifact_upload_failed。"""
    local = tmp_path / "app.tar.gz"
    local.write_bytes(b"data")

    sftp = FakeSftp()
    fail_conn = FakeConn(sftp, fail_connect=True)
    xfer, _ = _make_transfer(connector=_make_connector(fail_conn))

    with pytest.raises(AppError) as exc_info:
        await xfer.upload(str(local), "/tmp/axon-artifacts/x.tar.gz")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "artifact_upload_failed"


@pytest.mark.asyncio
async def test_upload_sftp_put_failure(tmp_path: Path):
    """SFTP put 失败：翻译为 502 artifact_upload_failed。"""
    local = tmp_path / "app.tar.gz"
    local.write_bytes(b"data")

    sftp = FakeSftp()
    sftp.set_put_error(OSError("disk full"))
    xfer, _ = _make_transfer(connector=_make_connector(FakeConn(sftp)))

    with pytest.raises(AppError) as exc_info:
        await xfer.upload(str(local), "/tmp/axon-artifacts/x.tar.gz")

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_upload_key_auth_passes_client_key(tmp_path: Path):
    """auth_type='key' 时 connector 收到 client_key 参数。"""
    local = tmp_path / "app.tar.gz"
    local.write_bytes(b"data")

    captured: dict = {}

    def recording_connector(**kwargs: Any) -> FakeConn:
        captured.update(kwargs)
        return FakeConn(FakeSftp())

    xfer, _ = _make_transfer(auth_type="key", connector=recording_connector, credential="pem-key")
    await xfer.upload(str(local), "/tmp/x.tar.gz")

    assert "client_key" in captured
    assert captured["client_key"] == "pem-key"
    assert "password" not in captured


@pytest.mark.asyncio
async def test_upload_password_auth_passes_password(tmp_path: Path):
    """auth_type='password' 时 connector 收到 password 参数。"""
    local = tmp_path / "app.tar.gz"
    local.write_bytes(b"data")

    captured: dict = {}

    def recording_connector(**kwargs: Any) -> FakeConn:
        captured.update(kwargs)
        return FakeConn(FakeSftp())

    xfer, _ = _make_transfer(
        auth_type="password", connector=recording_connector, credential="secret123"
    )
    await xfer.upload(str(local), "/tmp/x.tar.gz")

    assert "password" in captured
    assert captured["password"] == "secret123"
    assert "client_key" not in captured
