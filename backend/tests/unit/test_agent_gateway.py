"""T1.5 AgentGateway 占位实现。

MVP 阶段 Agent 未接入(§5.3):AgentGateway 实现统一 Executor 接口,
但所有动作抛明确的"未接入 Agent"错误,保证 access_mode=agent 的操作
返回清晰提示、不影响 SSH 路径。后续 T4.3 用真实 gRPC 实现替换本占位,
上层业务与 UI 零改动。
"""

import pytest

from app.adapters.agent_gateway import AgentGateway, AgentNotConnectedError
from app.adapters.executor import CommandResult, DeploySpec, Executor
from app.core.errors import AppError
from app.services.agent_connection import AgentConnectionManager, AgentRoutingError


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


async def test_redis_routing_failure_is_typed_service_unavailable():
    class _UnavailableManager(AgentConnectionManager):
        async def send_command(self, agent_id, command):
            del agent_id, command
            raise AgentRoutingError("redis down")

    gateway = AgentGateway(manager=_UnavailableManager(), agent_id="agent-1")

    with pytest.raises(AppError) as caught:
        await gateway.exec("uptime")

    assert caught.value.code == "agent_routing_unavailable"
    assert caught.value.status_code == 503


def test_not_connected_error_is_app_error():
    """AgentNotConnectedError 应是 AppError 子类,携带机器可读 code,
    经统一异常处理器返回明确 envelope 而非 500。"""
    from app.core.errors import AppError

    err = AgentNotConnectedError()
    assert isinstance(err, AppError)
    assert err.code == "agent_not_connected"
    assert err.status_code == 501


async def test_upload_artifact_sends_bounded_checksummed_chunks(tmp_path, monkeypatch):
    artifact = tmp_path / "app.tar.gz"
    artifact.write_bytes(b"abcdefgh")
    gateway = AgentGateway(artifact_chunk_bytes=3, artifact_max_bytes=16)
    calls: list[tuple[str, dict[str, str]]] = []

    async def fake_dispatch(action: str, params: dict[str, str]) -> CommandResult:
        calls.append((action, params))
        return CommandResult(exit_code=0, stdout="ok", stderr="")

    monkeypatch.setattr(gateway, "_dispatch", fake_dispatch)

    await gateway.upload_artifact(str(artifact), "/tmp/axon-artifacts/app.tar.gz")

    assert [action for action, _ in calls] == [
        "artifact_begin",
        "artifact_chunk",
        "artifact_chunk",
        "artifact_chunk",
        "artifact_commit",
    ]
    begin = calls[0][1]
    assert begin["remote_path"] == "/tmp/axon-artifacts/app.tar.gz"
    assert begin["size"] == "8"
    assert len(begin["sha256"]) == 64
    assert [call[1]["offset"] for call in calls[1:4]] == ["0", "3", "6"]
    assert all(len(call[1]["chunk_sha256"]) == 64 for call in calls[1:4])
    assert calls[-1][1]["transfer_id"] == begin["transfer_id"]


async def test_upload_artifact_rejects_oversized_file(tmp_path):
    artifact = tmp_path / "large.tar.gz"
    artifact.write_bytes(b"0123456789")
    gateway = AgentGateway(artifact_chunk_bytes=3, artifact_max_bytes=8)

    with pytest.raises(Exception, match="超过 Agent 制品上限"):
        await gateway.upload_artifact(str(artifact), "/tmp/axon-artifacts/large.tar.gz")
