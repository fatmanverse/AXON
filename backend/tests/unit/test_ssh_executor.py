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


async def test_update_config_writes_content_verbatim_even_with_delimiter():
    """配置内容含 heredoc 分隔符或 shell 元字符时,仍须原样写入(无注入/无截断)。

    旧实现用 `<<'YIMAI_EOF'` heredoc:若内容里恰有一行 `YIMAI_EOF`,heredoc 提前
    终止,残余内容被当命令执行——命令注入/文件损坏。改用 base64 编码传输后,
    无论内容含什么都安全。本测试从下发命令里还原内容,断言与原文逐字节一致。
    """
    import base64
    import re

    store, cred_id = _store_with_key()
    conn = FakeConnection()
    executor = SSHExecutor(_target(cred_id), store, connector=lambda **_: conn)

    # 恶意/棘手内容:含 heredoc 分隔符行、命令替换、单引号
    content = "A=1\nYIMAI_EOF\nrm -rf /\n$(whoami)\nit's a 'trap'\n"
    await executor.update_config("/etc/app/app.env", content)

    assert conn.ran, "应发出写配置命令"
    command = conn.ran[-1]
    # 命令里不得出现可被 shell 解释的原始危险行(须已编码)
    assert "rm -rf /" not in command
    assert "$(whoami)" not in command
    # 从命令中提取 base64 载荷并还原,须与原文逐字节一致。
    # base64 载荷全是安全字符,shlex.quote 不加引号,故直接匹配 `printf %s <payload>`。
    match = re.search(r"printf %s ([A-Za-z0-9+/=]{8,})", command)
    assert match, f"命令应含 base64 载荷: {command}"
    decoded = base64.b64decode(match.group(1)).decode()
    assert decoded == content


class _FlakyConnector:
    """前 fail_times 次建连(__aenter__)抛错,之后成功。用于验证建连重试。"""

    def __init__(self, *, fail_times: int, exc: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.exc = exc or OSError("connection refused")
        self.enter_calls = 0
        self.conn = FakeConnection()

    def __call__(self, **_):
        return self

    async def __aenter__(self):
        self.enter_calls += 1
        if self.enter_calls <= self.fail_times:
            raise self.exc
        return self.conn

    async def __aexit__(self, *exc):
        return None


def _retry_target(cred_id: str, *, retries: int) -> SSHTarget:
    # retry_backoff=0 让测试无需真实等待
    return SSHTarget(
        host="10.0.0.5",
        port=22,
        username="ops",
        credential_id=cred_id,
        max_connect_retries=retries,
        retry_backoff=0.0,
    )


async def test_connect_retries_then_succeeds():
    """建连前两次失败、第三次成功:在 max_connect_retries=2 下最终成功执行。"""
    store, cred_id = _store_with_key()
    connector = _FlakyConnector(fail_times=2)
    executor = SSHExecutor(_retry_target(cred_id, retries=2), store, connector=connector)

    result = await executor.exec("uptime")

    assert result.succeeded is True
    assert connector.enter_calls == 3  # 2 次失败 + 1 次成功


async def test_connect_retries_exhausted_raises():
    """建连始终失败:重试用尽后抛 AppError(502),尝试次数 = 1 + max_connect_retries。"""
    store, cred_id = _store_with_key()
    connector = _FlakyConnector(fail_times=99)
    executor = SSHExecutor(_retry_target(cred_id, retries=2), store, connector=connector)

    with pytest.raises(AppError, match="连接失败"):
        await executor.exec("uptime")

    assert connector.enter_calls == 3  # 1 + 2 retries


async def test_command_timeout_not_retried():
    """命令阶段超时(连接已建立)不重试:只尝试一次建连,直接 504。"""
    store, cred_id = _store_with_key()
    connector = _FlakyConnector(fail_times=0)  # 建连总成功
    connector.conn = FakeConnection(delay=0.2)  # 命令跑得慢 → 超时
    executor = SSHExecutor(_retry_target(cred_id, retries=3), store, connector=connector)

    with pytest.raises(AppError, match="超时"):
        await executor.exec("sleep 10", timeout=0.01)

    assert connector.enter_calls == 1  # 命令超时不触发建连重试


async def test_nonzero_exit_not_retried():
    """命令非零退出不是异常、不重试:只建连一次,返回失败结果。"""
    store, cred_id = _store_with_key()
    connector = _FlakyConnector(fail_times=0)
    connector.conn = FakeConnection(
        results={"false": FakeProcess(exit_status=1, stdout="", stderr="boom")}
    )
    executor = SSHExecutor(_retry_target(cred_id, retries=3), store, connector=connector)

    result = await executor.exec("false")

    assert result.succeeded is False
    assert connector.enter_calls == 1


# ── SSH 密码认证(需求3):auth_type 区分 key/password,机密都从保险箱取 ──


async def test_password_auth_passes_password_not_client_key():
    """auth_type=password 时,连接参数带 password(取自保险箱)而非 client_key。"""
    store = LocalSecretStore(master_key=generate_master_key())
    cred_id = store.put("ssh-pw", "s3cr3t-pw")
    captured: dict[str, object] = {}

    def connector(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    target = SSHTarget(
        host="10.0.0.6",
        port=22,
        username="ops",
        credential_id=cred_id,
        auth_type="password",
    )
    executor = SSHExecutor(target, store, connector=connector)
    await executor.exec("whoami")

    # 密码来自保险箱;绝不传 client_key(否则 asyncssh 会尝试私钥认证)
    assert captured["password"] == "s3cr3t-pw"
    assert "client_key" not in captured
    assert captured["username"] == "ops"


async def test_key_auth_is_default_and_passes_client_key():
    """不指定 auth_type 时默认私钥认证:连接参数带 client_key 而非 password(行为不变)。"""
    store, cred_id = _store_with_key()
    captured: dict[str, object] = {}

    def connector(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    executor = SSHExecutor(_target(cred_id), store, connector=connector)
    await executor.exec("whoami")

    assert "BEGIN PRIVATE KEY" in captured["client_key"]
    assert "password" not in captured
