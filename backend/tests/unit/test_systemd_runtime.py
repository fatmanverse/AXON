"""T1.7 systemd 运行时适配。

用 fake executor 记录收到的命令,验证:生命周期动作生成正确的 systemctl
命令、status 解析 active/inactive/failed、失败动作抛 AppError、unit_name
经 shlex 转义防命令注入。真实 systemd 集成验收另行,单测不触碰真实主机。
"""

import pytest

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.adapters.systemd_runtime import SystemdRuntime
from app.core.errors import AppError


class FakeExecutor(Executor):
    """模拟统一执行器:记录跑过的命令,按命令返回预置结果。"""

    def __init__(self, results: dict[str, CommandResult] | None = None) -> None:
        self._results = results or {}
        self.ran: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        return self._results.get(command, CommandResult(exit_code=0, stdout="", stderr=""))

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover - 未用到
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:
        raise NotImplementedError  # pragma: no cover - 未用到

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        raise NotImplementedError  # pragma: no cover - 未用到


UNIT = "billing.service"


async def test_start_issues_systemctl_start():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)

    await runtime.start(UNIT)

    assert executor.ran == [f"systemctl start {UNIT}"]


async def test_stop_issues_systemctl_stop():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)

    await runtime.stop(UNIT)

    assert executor.ran == [f"systemctl stop {UNIT}"]


async def test_restart_issues_systemctl_restart():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)

    await runtime.restart(UNIT)

    assert executor.ran == [f"systemctl restart {UNIT}"]


async def test_delete_disables_and_stops_unit():
    """systemd 无「删除」概念,delete 语义映射为停用并取消开机自启。"""
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)

    await runtime.delete(UNIT)

    assert executor.ran == [f"systemctl disable --now {UNIT}"]


async def test_status_active_reports_running():
    cmd = f"systemctl is-active {UNIT}"
    executor = FakeExecutor(results={cmd: CommandResult(exit_code=0, stdout="active\n", stderr="")})
    runtime = SystemdRuntime(executor)

    status = await runtime.status(UNIT)

    assert isinstance(status, ServiceStatus)
    assert status.name == UNIT
    assert status.running is True
    assert status.detail == "active"


async def test_status_inactive_reports_not_running():
    cmd = f"systemctl is-active {UNIT}"
    executor = FakeExecutor(
        results={cmd: CommandResult(exit_code=3, stdout="inactive\n", stderr="")}
    )
    runtime = SystemdRuntime(executor)

    status = await runtime.status(UNIT)

    assert status.running is False
    assert status.detail == "inactive"


async def test_status_failed_reports_not_running():
    """failed 状态非 active,应判为 running=False,且不抛错。"""
    cmd = f"systemctl is-active {UNIT}"
    executor = FakeExecutor(results={cmd: CommandResult(exit_code=3, stdout="failed\n", stderr="")})
    runtime = SystemdRuntime(executor)

    status = await runtime.status(UNIT)

    assert status.running is False
    assert status.detail == "failed"


async def test_status_does_not_raise_on_nonzero_exit():
    """is-active 对未运行服务本就返回非 0,status 不能因此抛错。"""
    cmd = f"systemctl is-active {UNIT}"
    executor = FakeExecutor(
        results={cmd: CommandResult(exit_code=3, stdout="unknown\n", stderr="boom")}
    )
    runtime = SystemdRuntime(executor)

    status = await runtime.status(UNIT)

    assert status.running is False


@pytest.mark.parametrize("action", ["start", "stop", "restart", "delete"])
async def test_lifecycle_action_raises_app_error_on_failure(action: str):
    """生命周期动作返回非 0 应抛 AppError,并携带 systemctl 的 stderr。"""
    executor = FakeExecutor()
    # 让任何命令都失败:用一个总返回非 0 的 executor
    executor._results = {}

    class FailingExecutor(FakeExecutor):
        async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
            self.ran.append(command)
            return CommandResult(exit_code=1, stdout="", stderr="Unit not found")

    runtime = SystemdRuntime(FailingExecutor())

    with pytest.raises(AppError, match="Unit not found") as excinfo:
        await getattr(runtime, action)(UNIT)

    assert excinfo.value.code == "systemd_action_failed"


async def test_unit_name_is_shell_escaped():
    """恶意 unit_name 必须被 shlex 转义,避免命令注入。"""
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)
    malicious = "evil; rm -rf /"

    await runtime.start(malicious)

    assert executor.ran == ["systemctl start 'evil; rm -rf /'"]


async def test_status_unit_name_is_shell_escaped():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)
    malicious = "evil$(whoami)"

    await runtime.status(malicious)

    assert executor.ran == ["systemctl is-active 'evil$(whoami)'"]
