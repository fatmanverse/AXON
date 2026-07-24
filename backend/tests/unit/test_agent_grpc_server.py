from __future__ import annotations

from pathlib import Path

from app.services.agent_connection import AgentConnectionManager
from app.services.agent_grpc_server import AgentGrpcServer


class _FakeGrpcServer:
    def __init__(self) -> None:
        self.secure_port_args: tuple[str, object] | None = None
        self.insecure_port_args: str | None = None
        self.started = False

    def add_generic_rpc_handlers(self, handlers: object) -> None:
        pass

    def add_registered_method_handlers(self, name: str, handlers: object) -> None:
        pass

    def add_secure_port(self, address: str, credentials: object) -> int:
        self.secure_port_args = (address, credentials)
        return 8443

    def add_insecure_port(self, address: str) -> int:
        self.insecure_port_args = address
        return 8080

    async def start(self) -> None:
        self.started = True

    async def stop(self, grace: float) -> None:
        self.started = False


async def test_server_uses_mutual_tls_credentials(tmp_path: Path, monkeypatch) -> None:
    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    ca_file = tmp_path / "client-ca.crt"
    cert_file.write_bytes(b"server-cert")
    key_file.write_bytes(b"server-key")
    ca_file.write_bytes(b"client-ca")

    fake_server = _FakeGrpcServer()
    captured: dict[str, object] = {}
    credentials = object()

    def fake_credentials(
        key_cert_pairs: object,
        root_certificates: bytes,
        require_client_auth: bool,
    ) -> object:
        captured.update(
            key_cert_pairs=key_cert_pairs,
            root_certificates=root_certificates,
            require_client_auth=require_client_auth,
        )
        return credentials

    monkeypatch.setattr("app.services.agent_grpc_server.grpc.aio.server", lambda: fake_server)
    monkeypatch.setattr(
        "app.services.agent_grpc_server.grpc.ssl_server_credentials",
        fake_credentials,
    )

    server = AgentGrpcServer(
        AgentConnectionManager(),
        host="127.0.0.1",
        port=7443,
        tls_enabled=True,
        server_cert_file=str(cert_file),
        server_key_file=str(key_file),
        client_ca_file=str(ca_file),
    )
    await server.start()

    assert fake_server.secure_port_args == ("127.0.0.1:7443", credentials)
    assert fake_server.insecure_port_args is None
    assert captured == {
        "key_cert_pairs": ((b"server-key", b"server-cert"),),
        "root_certificates": b"client-ca",
        "require_client_auth": True,
    }
    assert server.bound_port == 8443


async def test_tls_server_rejects_missing_material() -> None:
    server = AgentGrpcServer(AgentConnectionManager(), tls_enabled=True)

    try:
        await server.start()
    except ValueError as exc:
        assert "required for mTLS" in str(exc)
    else:
        raise AssertionError("missing mTLS material must be rejected")
