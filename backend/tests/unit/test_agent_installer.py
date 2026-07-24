"""Agent 经 SSH 下发安装单测(需求4)。

照搬 node_exporter 自举模式:控制面经 SSH 在目标机上幂等安装 axon-agent。
关键差异:二进制从**控制面下载端点**拉取(离线分发,不走公网),download_url
由上层按 control_plane_base_url + 版本组装并注入。
"""

import pytest

from app.adapters.agent_installer import AgentInstaller, _bootstrap_script
from app.adapters.executor import CommandResult
from app.core.errors import AppError


class _FakeExecutor:
    def __init__(self, result: CommandResult) -> None:
        self._result = result
        self.commands: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.commands.append(command)
        return self._result

    async def deploy(self, spec):  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path, content):  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref):  # pragma: no cover
        raise NotImplementedError


def test_bootstrap_script_is_idempotent_and_downloads_from_control_plane():
    url = "http://control-plane:8000/api/dist/axon-agent-1.0.0-linux-amd64"
    script = _bootstrap_script(
        download_url=url,
        version="1.0.0",
        install_dir="/usr/local/bin",
        service_name="axon-agent",
    )
    # 幂等:先探测已装
    assert "command -v axon-agent" in script
    assert "systemctl enable --now axon-agent" in script
    # 从控制面下载端点拉取,不走 github 公网
    assert url in script
    assert "github.com" not in script


def test_bootstrap_script_quotes_download_url():
    # 恶意 URL 中的 shell 元字符须被 shlex.quote 转义,杜绝命令注入
    url = "http://x/api/dist/a; rm -rf /"
    script = _bootstrap_script(
        download_url=url,
        version="1.0.0",
        install_dir="/usr/local/bin",
        service_name="axon-agent",
    )
    assert "rm -rf /;" not in script.replace(url, "")  # 原始注入片段不裸露为可执行命令


def test_bootstrap_script_passes_explicit_agent_transport_args():
    script = _bootstrap_script(
        download_url="http://cp/agent",
        version="1.0.0",
        exec_args=("--agent-id", "node-1", "--insecure"),
    )

    assert "ExecStart=/usr/local/bin/axon-agent --agent-id node-1 --insecure" in script


async def test_ensure_installed_runs_script_and_succeeds():
    executor = _FakeExecutor(CommandResult(exit_code=0, stdout="done", stderr=""))
    installer = AgentInstaller(executor)
    await installer.ensure_installed(
        download_url="http://cp:8000/api/dist/axon-agent-1.0.0-linux-amd64",
        version="1.0.0",
    )
    assert len(executor.commands) == 1
    assert executor.commands[0].startswith("set -e")


async def test_ensure_installed_raises_on_failure():
    executor = _FakeExecutor(CommandResult(exit_code=1, stdout="", stderr="boom"))
    installer = AgentInstaller(executor)
    with pytest.raises(AppError, match="agent 安装失败"):
        await installer.ensure_installed(
            download_url="http://cp:8000/api/dist/axon-agent-1.0.0-linux-amd64",
            version="1.0.0",
        )
