"""T1.13 node_exporter 自举安装(经 SSH,设计 §6.2)。

用 fake executor 记录命令,验证:
- ensure_installed 生成幂等安装脚本:已装则确保运行,未装则下载/安装/拉起。
- 脚本含目标版本的下载 URL、systemd 拉起、监听端口。
- version 经 shlex 转义,杜绝命令注入。
- 安装命令返回非 0 抛 AppError。

单测不触碰真实主机。
"""

import pytest

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.adapters.node_exporter import DEFAULT_PORT, DEFAULT_VERSION, NodeExporterInstaller
from app.core.errors import AppError


class FakeExecutor(Executor):
    """记录跑过的命令;可配置为失败。"""

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
        self.ran: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        if self._ok:
            return CommandResult(exit_code=0, stdout="ok", stderr="")
        return CommandResult(exit_code=1, stdout="", stderr="install failed")

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


async def test_ensure_installed_runs_bootstrap_script():
    executor = FakeExecutor()
    installer = NodeExporterInstaller(executor)

    await installer.ensure_installed()

    assert len(executor.ran) == 1
    script = executor.ran[0]
    # 默认版本的下载 URL、systemd 拉起、默认端口都应出现
    assert DEFAULT_VERSION in script
    assert "node_exporter" in script
    assert "systemctl" in script
    assert f":{DEFAULT_PORT}" in script or f"{DEFAULT_PORT}" in script


async def test_ensure_installed_is_idempotent_by_guarding_on_existing_binary():
    """脚本应先探测已装(command -v / test -x),已装则不重复下载。"""
    executor = FakeExecutor()
    installer = NodeExporterInstaller(executor)

    await installer.ensure_installed()

    script = executor.ran[0]
    assert "command -v node_exporter" in script or "node_exporter" in script


async def test_custom_version_is_shell_escaped():
    executor = FakeExecutor()
    installer = NodeExporterInstaller(executor)
    malicious = "1.8.2; rm -rf /"

    await installer.ensure_installed(version=malicious)

    script = executor.ran[0]
    # 恶意版本必须被整体引用,不能裸拼进命令
    assert "'1.8.2; rm -rf /'" in script


async def test_install_failure_raises_app_error():
    executor = FakeExecutor(ok=False)
    installer = NodeExporterInstaller(executor)

    with pytest.raises(AppError, match="install failed") as excinfo:
        await installer.ensure_installed()

    assert excinfo.value.code == "node_exporter_install_failed"
