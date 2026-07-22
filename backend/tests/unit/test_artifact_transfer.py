"""窄 SSH/SFTP 制品传输适配器与共享 factory 组装。"""

from pathlib import Path

import asyncssh
import pytest

from app.adapters.agent_gateway import AgentGateway
from app.adapters.artifact_transfer import SshArtifactTransfer
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.errors import AppError
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.server import AccessMode, Server
from app.services.executor_factory import (
    build_artifact_transfer_for_server,
    build_executor_for_server,
    build_ssh_target_for_server,
)


class FakeSFTP:
    def __init__(self, *, put_error: Exception | None = None) -> None:
        self._put_error = put_error
        self.makedirs_calls: list[tuple[str, bool]] = []
        self.put_calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> "FakeSFTP":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def makedirs(self, path: str, *, exist_ok: bool = False) -> None:
        self.makedirs_calls.append((path, exist_ok))

    async def put(self, local_path: str, remote_path: str) -> None:
        self.put_calls.append((local_path, remote_path))
        if self._put_error is not None:
            raise self._put_error


class FakeConnection:
    def __init__(self, sftp: FakeSFTP) -> None:
        self._sftp = sftp

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def start_sftp_client(self) -> FakeSFTP:
        return self._sftp


class FakeConnector:
    def __init__(self, sftp: FakeSFTP) -> None:
        self._connection = FakeConnection(sftp)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs) -> FakeConnection:
        self.calls.append(kwargs)
        return self._connection


def _secret_store(secret: str = "-----BEGIN PRIVATE KEY-----\nfake\n"):
    store = LocalSecretStore(master_key=generate_master_key())
    credential_id = store.put("ssh-credential", secret)
    return store, credential_id


def _ssh_server(credential_id: str, *, auth_type: str = "key") -> Server:
    return Server(
        name="systemd-01",
        host="10.0.0.8",
        access_mode=AccessMode.SSH,
        ssh_credential_id=credential_id,
        labels={
            "ssh_port": 2222,
            "ssh_username": "deploy",
            "auth_type": auth_type,
        },
    )


async def test_upload_creates_remote_parent_and_puts_local_file(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"artifact bytes")
    store, credential_id = _secret_store()
    sftp = FakeSFTP()
    connector = FakeConnector(sftp)
    transfer = SshArtifactTransfer(
        SSHTarget("10.0.0.8", 2222, "deploy", credential_id),
        store,
        connector=connector,
    )

    await transfer.upload(str(artifact), "/tmp/axon-artifacts/a1.tar.gz")

    assert sftp.makedirs_calls == [("/tmp/axon-artifacts", True)]
    assert sftp.put_calls == [(str(artifact), "/tmp/axon-artifacts/a1.tar.gz")]
    assert connector.calls == [
        {
            "host": "10.0.0.8",
            "port": 2222,
            "username": "deploy",
            "connect_timeout": 10.0,
            "known_hosts": None,
            "client_keys": [b"-----BEGIN PRIVATE KEY-----\nfake\n"],
        }
    ]
    assert isinstance(connector.calls[0]["client_keys"], list)
    assert "password" not in connector.calls[0]


async def test_upload_uses_password_auth_without_client_keys(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.touch()
    store, credential_id = _secret_store("s3cr3t")
    connector = FakeConnector(FakeSFTP())
    transfer = SshArtifactTransfer(
        SSHTarget(
            "10.0.0.8",
            22,
            "deploy",
            credential_id,
            auth_type="password",
        ),
        store,
        connector=connector,
    )

    await transfer.upload(str(artifact), "/tmp/artifact.tar.gz")

    assert connector.calls[0]["password"] == "s3cr3t"
    assert "client_keys" not in connector.calls[0]


def test_asyncssh_accepts_in_memory_private_key_bytes():
    private_key = asyncssh.generate_private_key("ssh-ed25519").export_private_key()

    options = asyncssh.SSHClientConnectionOptions(client_keys=[private_key])

    assert len(options.client_keys) == 1


async def test_upload_missing_local_file_raises_404_before_connecting(tmp_path: Path):
    store, credential_id = _secret_store()
    connector = FakeConnector(FakeSFTP())
    transfer = SshArtifactTransfer(
        SSHTarget("10.0.0.8", 22, "deploy", credential_id),
        store,
        connector=connector,
    )

    with pytest.raises(AppError) as caught:
        await transfer.upload(str(tmp_path / "missing.tar.gz"), "/tmp/artifact.tar.gz")

    assert caught.value.code == "artifact_file_not_found"
    assert caught.value.status_code == 404
    assert connector.calls == []


async def test_upload_rejects_symlink_before_connecting(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.touch()
    symlink = tmp_path / "artifact-link.tar.gz"
    symlink.symlink_to(artifact)
    store, credential_id = _secret_store()
    connector = FakeConnector(FakeSFTP())
    transfer = SshArtifactTransfer(
        SSHTarget("10.0.0.8", 22, "deploy", credential_id),
        store,
        connector=connector,
    )

    with pytest.raises(AppError) as caught:
        await transfer.upload(str(symlink), "/tmp/artifact.tar.gz")

    assert caught.value.code == "artifact_file_not_found"
    assert caught.value.status_code == 404
    assert connector.calls == []


async def test_upload_translates_sftp_failure_to_502(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.touch()
    store, credential_id = _secret_store()
    failure = OSError("disk full")
    transfer = SshArtifactTransfer(
        SSHTarget("10.0.0.8", 22, "deploy", credential_id),
        store,
        connector=FakeConnector(FakeSFTP(put_error=failure)),
    )

    with pytest.raises(AppError) as caught:
        await transfer.upload(str(artifact), "/tmp/artifact.tar.gz")

    assert caught.value.code == "artifact_upload_failed"
    assert caught.value.status_code == 502
    assert caught.value.__cause__ is failure


async def test_artifact_transfer_factory_builds_ssh_transfer(tmp_path: Path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.touch()
    store, credential_id = _secret_store("ssh-password")
    connector = FakeConnector(FakeSFTP())

    transfer = build_artifact_transfer_for_server(
        _ssh_server(credential_id, auth_type="password"),
        store,
        connector=connector,
    )
    await transfer.upload(str(artifact), "/tmp/artifact.tar.gz")

    assert isinstance(transfer, SshArtifactTransfer)
    assert connector.calls[0]["host"] == "10.0.0.8"
    assert connector.calls[0]["port"] == 2222
    assert connector.calls[0]["username"] == "deploy"
    assert connector.calls[0]["password"] == "ssh-password"


@pytest.mark.parametrize(
    "server",
    [
        None,
        Server(
            name="agent-01",
            host="10.0.0.9",
            access_mode=AccessMode.AGENT,
            agent_id="agent-01",
            labels={},
        ),
    ],
)
def test_artifact_transfer_factory_rejects_non_ssh_server(server: Server | None):
    store, _ = _secret_store()

    with pytest.raises(AppError) as caught:
        build_artifact_transfer_for_server(server, store)

    assert caught.value.code == "artifact_transfer_not_supported"
    assert caught.value.status_code == 501


def test_executor_factory_reuses_shared_ssh_target_without_behavior_change():
    store, credential_id = _secret_store()
    server = _ssh_server(credential_id)

    target = build_ssh_target_for_server(server)
    executor = build_executor_for_server(server, store, connector=FakeConnector(FakeSFTP()))

    assert target == SSHTarget(
        host="10.0.0.8",
        port=2222,
        username="deploy",
        credential_id=credential_id,
        auth_type="key",
    )
    assert isinstance(executor, SSHExecutor)
    assert executor._target == target


def test_executor_factory_keeps_agent_gateway_behavior():
    store, _ = _secret_store()
    server = Server(
        name="agent-01",
        host="10.0.0.9",
        access_mode=AccessMode.AGENT,
        agent_id="agent-01",
        labels={},
    )

    assert isinstance(build_executor_for_server(server, store), AgentGateway)
