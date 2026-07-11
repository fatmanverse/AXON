"""T1.4 SSHExecutor 实现。

用 fake 连接验证:私钥从保险箱取(不落明文)、命令执行捕获
stdout/stderr/exit、超时处理、连通性测试。真实 sshd 集成验收另行(容器)。
连接层通过 connector 注入,单测不触碰真实网络。
"""

import asyncio

import pytest

from app.adapters.executor import DeploySpec, Executor
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.errors import AppError
from app.core.secrets import LocalSecretStore, generate_master_key


class FakeProcess:
    """模拟 asyncssh run() 的返回:携带 exit/stdout/stderr。"""

    def __init__(self, exit_status: int, stdout: str, stderr: str) -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class FakeConnection:
    """模拟 asyncssh 连接:记录建连参数与执行的命令。"""

    def __init__(
        self, *, results: dict[str, FakeProcess] | None = None, delay: float = 0.0
    ) -> None:
        self._results = results or {}
        self._delay = delay
        self.ran: list[str] = []
        self.closed = False

    async def run(self, command: str, *, timeout: float | None = None) -> FakeProcess:
        self.ran.append(command)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._results.get(
            command, FakeProcess(exit_status=0, stdout=f"ran:{command}", stderr="")
        )

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True


def _store_with_key() -> tuple[LocalSecretStore, str]:
    store = LocalSecretStore(master_key=generate_master_key())
    cred_id = store.put("ssh-key", "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")
    return store, cred_id


def _target(cred_id: str) -> SSHTarget:
    return SSHTarget(host="10.0.0.5", port=22, username="ops", credential_id=cred_id)


async def test_ssh_executor_is_an_executor():
    store, cred_id = _store_with_key()
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: FakeConnection())
    assert isinstance(executor, Executor)


async def test_exec_captures_exit_stdout_stderr():
    store, cred_id = _store_with_key()
    conn = FakeConnection(
        results={"uptime": FakeProcess(exit_status=0, stdout="up 3 days", stderr="")}
    )
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: conn)

    result = await executor.exec("uptime")

    assert result.exit_code == 0
    assert result.stdout == "up 3 days"
    assert result.succeeded is True
    assert conn.ran == ["uptime"]


async def test_exec_nonzero_exit_marks_failure():
    store, cred_id = _store_with_key()
    conn = FakeConnection(results={"false": FakeProcess(exit_status=1, stdout="", stderr="boom")})
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: conn)

    result = await executor.exec("false")

    assert result.exit_code == 1
    assert result.stderr == "boom"
    assert result.succeeded is False


async def test_private_key_pulled_from_vault_not_plaintext():
    """连接时私钥应来自保险箱按 credential_id 取,而非明文传入。"""
    store, cred_id = _store_with_key()
    captured: dict[str, object] = {}

    def connector(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    executor = SSHExecutor(_target(cred_id), store, connector=connector)
    await executor.exec("whoami")

    # 连接参数里带的是从保险箱取出的私钥内容,且 executor 本身不持有明文
    assert "BEGIN PRIVATE KEY" in captured["client_key"]
    assert captured["host"] == "10.0.0.5"
    assert captured["username"] == "ops"


async def test_exec_timeout_raises_app_error():
    store, cred_id = _store_with_key()
    conn = FakeConnection(delay=0.2)
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: conn)

    with pytest.raises(AppError, match="超时"):
        await executor.exec("sleep 10", timeout=0.01)


async def test_test_connectivity_returns_true_on_success():
    store, cred_id = _store_with_key()
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: FakeConnection())
    assert await executor.test_connectivity() is True


async def test_test_connectivity_returns_false_on_connect_error():
    store, cred_id = _store_with_key()

    def connector(**_):
        raise OSError("connection refused")

    executor = SSHExecutor(_target(cred_id), store, connector=connector)
    assert await executor.test_connectivity() is False


async def test_deploy_and_status_use_exec():
    store, cred_id = _store_with_key()
    conn = FakeConnection()
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: conn)

    await executor.deploy(DeploySpec(artifact="registry/app:v1", env={"K": "V"}))
    status = await executor.get_service_status("app.service")

    # 部署与状态查询最终都落到 SSH 命令执行
    assert any("registry/app:v1" in c for c in conn.ran)
    assert status.name == "app.service"
