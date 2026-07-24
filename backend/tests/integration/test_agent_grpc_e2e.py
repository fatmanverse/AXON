"""Agent gRPC 端到端验收(T4.1 wire,设计 §15.5/§5.4)。

起真实 grpc.aio server(端口 0 由 OS 分配)+ 真实 grpc.aio 客户端(充当 Agent),
验证整条 wire 打通:
- Agent 外连建双向流、发心跳 → 控制面置该 agent 在线。
- 控制面经 AgentGateway 下发命令 → 命令经 gRPC 流到达 Agent。
- Agent 回 result ACK → AgentGateway 的 exec 返回成功。

这是不打桩的真实网络往返,证明 servicer + server + manager + gateway 协同无误。
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.adapters.agent_gateway import AgentGateway
from app.grpc_gen import agent_pb2, agent_pb2_grpc
from app.services.agent_connection import AgentConnectionManager
from app.services.agent_grpc_server import AgentGrpcServer


@pytest.fixture(scope="module", autouse=True)
def require_local_socket_binding() -> None:
    """Run real-wire tests only where the host permits loopback listeners."""
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"local socket binding unavailable: {exc}")
    finally:
        probe.close()


def _issue_certificate(
    *,
    common_name: str,
    issuer_cert: x509.Certificate,
    issuer_key: rsa.RSAPrivateKey,
    key: rsa.RSAPrivateKey,
    usage: x509.ObjectIdentifier,
    dns_name: str | None = None,
) -> x509.Certificate:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(issuer_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    )
    if dns_name:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False
        )
    return builder.sign(issuer_key, hashes.SHA256())


def _pem_key(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def _write_mtls_material(tmp_path: Path, *, client_identity: str) -> dict[str, bytes | str]:
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Axon test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = _issue_certificate(
        common_name="localhost",
        issuer_cert=ca_cert,
        issuer_key=ca_key,
        key=server_key,
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
        dns_name="localhost",
    )
    client_cert = _issue_certificate(
        common_name=client_identity,
        issuer_cert=ca_cert,
        issuer_key=ca_key,
        key=client_key,
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    )
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    server_cert_pem = server_cert.public_bytes(serialization.Encoding.PEM)
    server_key_pem = _pem_key(server_key)
    client_cert_pem = client_cert.public_bytes(serialization.Encoding.PEM)
    client_key_pem = _pem_key(client_key)

    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    ca_file = tmp_path / "client-ca.crt"
    cert_file.write_bytes(server_cert_pem)
    key_file.write_bytes(server_key_pem)
    ca_file.write_bytes(ca_pem)
    return {
        "server_cert_file": str(cert_file),
        "server_key_file": str(key_file),
        "client_ca_file": str(ca_file),
        "ca_pem": ca_pem,
        "client_cert_pem": client_cert_pem,
        "client_key_pem": client_key_pem,
    }


async def _wait(predicate, *, timeout: float = 2.0) -> None:
    """轮询等待条件成立,超时抛。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


async def test_end_to_end_command_roundtrip():
    manager = AgentConnectionManager(heartbeat_timeout=100.0)
    server = AgentGrpcServer(manager, port=0)
    await server.start()
    port = server.bound_port
    assert port

    outbound: asyncio.Queue = asyncio.Queue()  # 测试驱动 agent 上行的消息
    agent_done = asyncio.Event()

    async def agent_upstream():
        # 首条心跳建流(带 agent_id)
        yield agent_pb2.AgentMessage(
            agent_id="agent-e2e",
            heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0"),
        )
        # 之后按测试指令上行(如 result ACK),直到收尾
        while True:
            msg = await outbound.get()
            if msg is None:
                return
            yield msg

    received_commands: list = []

    async def run_agent():
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            call = stub.Connect(agent_upstream())
            async for command in call:
                received_commands.append(command)
                # 收到命令 → 回 result ACK(模拟执行成功)
                await outbound.put(
                    agent_pb2.AgentMessage(
                        agent_id="agent-e2e",
                        ack=agent_pb2.CommandAck(
                            task_id=command.task_id,
                            kind=agent_pb2.ACK_KIND_RESULT,
                            ok=True,
                            detail="executed",
                        ),
                    )
                )
        agent_done.set()

    agent_task = asyncio.create_task(run_agent())
    try:
        # 等 agent 上线
        await _wait(lambda: manager.is_online("agent-e2e", now=manager_now()))

        # 控制面经 AgentGateway 下发命令,等 result ACK
        gateway = AgentGateway(manager=manager, agent_id="agent-e2e", ack_timeout=3.0, fence=1)
        result = await gateway.exec("systemctl restart billing")

        assert result.succeeded
        assert result.stdout == "executed"
        assert received_commands
        assert received_commands[0].action == "exec"
        assert received_commands[0].params["command"] == "systemctl restart billing"
    finally:
        await outbound.put(None)  # 让 agent 上行流收尾
        await server.stop()
        agent_task.cancel()
        try:
            await agent_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def manager_now() -> float:
    # 与 AgentGrpcServer 默认 clock(monotonic)一致
    from time import monotonic

    return monotonic()


async def test_server_start_stop_idempotent():
    manager = AgentConnectionManager()
    server = AgentGrpcServer(manager, port=0)
    await server.start()
    await server.start()  # 幂等:第二次不报错
    assert server.bound_port
    await server.stop()
    await server.stop()  # 幂等


async def test_mtls_client_certificate_identity_roundtrip(tmp_path: Path):
    material = _write_mtls_material(tmp_path, client_identity="agent-mtls")
    manager = AgentConnectionManager(heartbeat_timeout=100.0)
    server = AgentGrpcServer(
        manager,
        host="127.0.0.1",
        port=0,
        tls_enabled=True,
        server_cert_file=str(material["server_cert_file"]),
        server_key_file=str(material["server_key_file"]),
        client_ca_file=str(material["client_ca_file"]),
    )
    await server.start()
    assert server.bound_port

    stop = asyncio.Event()

    async def upstream():
        yield agent_pb2.AgentMessage(
            agent_id="agent-mtls",
            heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0"),
        )
        await stop.wait()

    credentials = grpc.ssl_channel_credentials(
        root_certificates=material["ca_pem"],
        private_key=material["client_key_pem"],
        certificate_chain=material["client_cert_pem"],
    )

    async def run_agent():
        async with grpc.aio.secure_channel(
            f"127.0.0.1:{server.bound_port}",
            credentials,
            options=(("grpc.ssl_target_name_override", "localhost"),),
        ) as channel:
            call = agent_pb2_grpc.AgentServiceStub(channel).Connect(upstream())
            async for _ in call:
                pass

    task = asyncio.create_task(run_agent())
    try:
        await _wait(lambda: manager.is_online("agent-mtls", now=manager_now()))
    finally:
        stop.set()
        await server.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_mtls_rejects_certificate_identity_mismatch(tmp_path: Path):
    material = _write_mtls_material(tmp_path, client_identity="different-agent")
    manager = AgentConnectionManager(heartbeat_timeout=100.0)
    server = AgentGrpcServer(
        manager,
        host="127.0.0.1",
        port=0,
        tls_enabled=True,
        server_cert_file=str(material["server_cert_file"]),
        server_key_file=str(material["server_key_file"]),
        client_ca_file=str(material["client_ca_file"]),
    )
    await server.start()
    assert server.bound_port

    async def upstream():
        yield agent_pb2.AgentMessage(
            agent_id="agent-mtls",
            heartbeat=agent_pb2.Heartbeat(agent_version="1.0.0"),
        )

    credentials = grpc.ssl_channel_credentials(
        root_certificates=material["ca_pem"],
        private_key=material["client_key_pem"],
        certificate_chain=material["client_cert_pem"],
    )
    try:
        async with grpc.aio.secure_channel(
            f"127.0.0.1:{server.bound_port}",
            credentials,
            options=(("grpc.ssl_target_name_override", "localhost"),),
        ) as channel:
            call = agent_pb2_grpc.AgentServiceStub(channel).Connect(upstream())
            with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                async for _ in call:
                    pass
            assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
    finally:
        await server.stop()
