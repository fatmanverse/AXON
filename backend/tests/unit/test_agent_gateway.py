"""T1.5 AgentGateway 占位实现。

MVP 阶段 Agent 未接入(§5.3):AgentGateway 实现统一 Executor 接口,
但所有动作抛明确的"未接入 Agent"错误,保证 access_mode=agent 的操作
返回清晰提示、不影响 SSH 路径。后续 T4.3 用真实 gRPC 实现替换本占位,
上层业务与 UI 零改动。
"""

import pytest

from app.adapters.agent_gateway import AgentGateway, AgentNotConnectedError
from app.adapters.executor import DeploySpec, Executor


def test_agent_gateway_is_an_executor():
    assert isinstance(AgentGateway(), Executor)


async def test_exec_raises_not_connected():
    gateway = AgentGateway()
    with pytest.raises(AgentNotConnectedError, match="未接入"):
        await gateway.exec("uptime")


async def test_deploy_raises_not_connected():
    gateway = AgentGateway()
    with pytest.raises(AgentNotConnectedError):
        await gateway.deploy(DeploySpec(artifact="registry/app:v1"))


async def test_update_config_raises_not_connected():
    gateway = AgentGateway()
    with pytest.raises(AgentNotConnectedError):
        await gateway.update_config("/etc/app.conf", "a=1")


async def test_get_service_status_raises_not_connected():
    gateway = AgentGateway()
    with pytest.raises(AgentNotConnectedError):
        await gateway.get_service_status("app.service")


def test_not_connected_error_is_app_error():
    """AgentNotConnectedError 应是 AppError 子类,携带机器可读 code,
    经统一异常处理器返回明确 envelope 而非 500。"""
    from app.core.errors import AppError

    err = AgentNotConnectedError()
    assert isinstance(err, AppError)
    assert err.code == "agent_not_connected"
    assert err.status_code == 501
