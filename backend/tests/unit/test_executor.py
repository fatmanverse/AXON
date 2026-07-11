"""T1.3 Executor 统一接口 + 工厂路由。

用 fake executor 验证:接口契约(exec/deploy/update_config/get_service_status)、
工厂按 server.access_mode 路由到正确实现、结果模型形态一致。
"""

import pytest

from app.adapters.executor import (
    CommandResult,
    DeploySpec,
    Executor,
    ExecutorFactory,
    ServiceStatus,
)
from app.models.server import AccessMode


class FakeExecutor(Executor):
    """测试替身:记录调用、返回可预期结果,不触碰真实 SSH/Agent。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.calls.append(f"exec:{command}")
        return CommandResult(exit_code=0, stdout=f"{self.label}:{command}", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        self.calls.append(f"deploy:{spec.artifact}")
        return CommandResult(exit_code=0, stdout=self.label, stderr="")

    async def update_config(self, path: str, content: str) -> CommandResult:
        self.calls.append(f"config:{path}")
        return CommandResult(exit_code=0, stdout=self.label, stderr="")

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        self.calls.append(f"status:{service_ref}")
        return ServiceStatus(name=service_ref, running=True, detail=self.label)


async def test_command_result_flags_success_by_exit_code():
    assert CommandResult(exit_code=0, stdout="ok", stderr="").succeeded is True
    assert CommandResult(exit_code=1, stdout="", stderr="boom").succeeded is False


async def test_factory_routes_ssh_mode_to_registered_builder():
    factory = ExecutorFactory()
    factory.register(AccessMode.SSH, lambda: FakeExecutor("ssh"))

    executor = factory.create(AccessMode.SSH)
    result = await executor.exec("uptime")

    assert isinstance(executor, FakeExecutor)
    assert executor.label == "ssh"
    assert result.stdout == "ssh:uptime"


async def test_factory_routes_agent_mode_to_registered_builder():
    factory = ExecutorFactory()
    factory.register(AccessMode.SSH, lambda: FakeExecutor("ssh"))
    factory.register(AccessMode.AGENT, lambda: FakeExecutor("agent"))

    executor = factory.create(AccessMode.AGENT)

    assert executor.label == "agent"


async def test_factory_raises_for_unregistered_mode():
    factory = ExecutorFactory()
    with pytest.raises(ValueError, match="未注册"):
        factory.create(AccessMode.SSH)


async def test_executor_interface_shape():
    executor = FakeExecutor("x")
    await executor.deploy(DeploySpec(artifact="registry/app:v1", env={"K": "V"}))
    await executor.update_config("/etc/app.conf", "a=1")
    status = await executor.get_service_status("app.service")

    assert status.running is True
    assert executor.calls == [
        "deploy:registry/app:v1",
        "config:/etc/app.conf",
        "status:app.service",
    ]
