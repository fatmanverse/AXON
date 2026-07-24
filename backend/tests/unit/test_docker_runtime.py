"""T1.8 docker 运行时适配。

与 systemd 同构:用 fake executor 记录命令,验证生命周期动作生成正确的
docker 命令、status 解析 running/exited/不存在、失败动作抛 AppError、
container_name 经 shlex 转义防命令注入。单测不触碰真实 docker daemon。
"""

import pytest

from app.adapters.docker_runtime import DockerRuntime
from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
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

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


CONTAINER = "billing"
_STATUS_CMD = f"docker inspect --format '{{{{.State.Running}}}}' {CONTAINER}"


async def test_start_issues_docker_start():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)

    await runtime.start(CONTAINER)

    assert executor.ran == [f"docker start {CONTAINER}"]


async def test_stop_issues_docker_stop():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)

    await runtime.stop(CONTAINER)

    assert executor.ran == [f"docker stop {CONTAINER}"]


async def test_restart_issues_docker_restart():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)

    await runtime.restart(CONTAINER)

    assert executor.ran == [f"docker restart {CONTAINER}"]


async def test_delete_force_removes_container():
    """docker 的删除语义:强制移除容器(rm -f),即使在运行也直接下线。"""
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)

    await runtime.delete(CONTAINER)

    assert executor.ran == [f"docker rm -f {CONTAINER}"]


async def test_status_running_reports_running():
    executor = FakeExecutor(
        results={_STATUS_CMD: CommandResult(exit_code=0, stdout="true\n", stderr="")}
    )
    runtime = DockerRuntime(executor)

    status = await runtime.status(CONTAINER)

    assert isinstance(status, ServiceStatus)
    assert status.name == CONTAINER
    assert status.running is True
    assert status.detail == "true"


async def test_status_exited_reports_not_running():
    executor = FakeExecutor(
        results={_STATUS_CMD: CommandResult(exit_code=0, stdout="false\n", stderr="")}
    )
    runtime = DockerRuntime(executor)

    status = await runtime.status(CONTAINER)

    assert status.running is False
    assert status.detail == "false"


async def test_status_missing_container_not_running_and_no_raise():
    """容器不存在时 docker inspect 返回非 0,status 不能抛错,判为 running=False。"""
    executor = FakeExecutor(
        results={
            _STATUS_CMD: CommandResult(
                exit_code=1, stdout="", stderr="Error: No such object: billing"
            )
        }
    )
    runtime = DockerRuntime(executor)

    status = await runtime.status(CONTAINER)

    assert status.running is False
    assert "No such object" in status.detail


@pytest.mark.parametrize("action", ["start", "stop", "restart", "delete"])
async def test_lifecycle_action_raises_app_error_on_failure(action: str):
    """生命周期动作返回非 0 应抛 AppError,并携带 docker 的 stderr。"""

    class FailingExecutor(FakeExecutor):
        async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
            self.ran.append(command)
            return CommandResult(exit_code=1, stdout="", stderr="No such container")

    runtime = DockerRuntime(FailingExecutor())

    with pytest.raises(AppError, match="No such container") as excinfo:
        await getattr(runtime, action)(CONTAINER)

    assert excinfo.value.code == "docker_action_failed"


async def test_container_name_is_shell_escaped():
    """恶意 container_name 必须被 shlex 转义,避免命令注入。"""
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)
    malicious = "evil; rm -rf /"

    await runtime.start(malicious)

    assert executor.ran == ["docker start 'evil; rm -rf /'"]


async def test_status_container_name_is_shell_escaped():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)
    malicious = "evil$(whoami)"

    await runtime.status(malicious)

    assert executor.ran == ["docker inspect --format '{{.State.Running}}' 'evil$(whoami)'"]
