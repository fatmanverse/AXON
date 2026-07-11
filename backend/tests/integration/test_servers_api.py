"""T1.6 服务器纳管 API 验收。

覆盖:
- 添加 SSH 服务器:私钥存保险箱、业务表只存 credential_id、响应不回私钥。
- 列表可见新增服务器。
- 连通性测试端点(注入 fake connector,不触真实 SSH)。
- 删除走鉴权 + 写审计。
- 未授权 401、无权限 403。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters import ssh_executor
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.audit import AuditResult
from app.models.base import Base
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-ops-key\n-----END PRIVATE KEY-----"


class _FakeProcess:
    def __init__(self, exit_status: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok

    async def run(self, command: str, *, timeout: float | None = None) -> _FakeProcess:
        if self._ok:
            return _FakeProcess(exit_status=0, stdout="active", stderr="")
        return _FakeProcess(exit_status=1, stdout="", stderr="fail")

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest_asyncio.fixture
async def app_client(monkeypatch, tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-servers",
        secret_backend="local",
        secret_master_key="",  # 自动生成
        rate_limit_enabled=False,
        # 纳管后自举会写 file_sd:指向 tmp,避免触碰真实 /etc/prometheus
        prometheus_targets_file=str(tmp_path / "nodes.json"),
    )
    app: FastAPI = create_app(settings)

    # 连通性测试默认注入可连通的 fake connector,避免真实 SSH
    monkeypatch.setattr(ssh_executor, "_default_connector", lambda **_: _FakeConnection(ok=True))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                svc = AuthService(session, settings)
                await svc.create_user("admin", "admin-pw", roles=["admin"])
                await svc.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_add_ssh_server_stores_key_in_vault(app_client):
    client, _, app = app_client
    token = await _token(client, "admin", "admin-pw")

    resp = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-01",
            "host": "10.0.0.10",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
            "labels": {"env": "prod"},
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    # 响应不回私钥;业务表存的是 credential_id 而非明文
    assert "ssh_private_key" not in data
    assert _FAKE_KEY not in resp.text
    assert data["ssh_credential_id"]
    # 保险箱里能按该 id 取回原始私钥
    store = app.state.secret_store
    assert store.get(data["ssh_credential_id"]) == _FAKE_KEY


async def test_list_servers_shows_added(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-02",
            "host": "10.0.0.11",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
        },
    )

    resp = await client.get("/api/servers", headers=_auth(token))
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["data"]]
    assert "web-02" in names


async def test_connectivity_test_endpoint_ok(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    created = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-03",
            "host": "10.0.0.12",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
        },
    )
    server_id = created.json()["data"]["id"]

    resp = await client.post(f"/api/servers/{server_id}/test-connection", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["data"]["reachable"] is True


async def test_delete_server_writes_audit(app_client):
    client, settings, app = app_client
    token = await _token(client, "admin", "admin-pw")
    created = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-04",
            "host": "10.0.0.13",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
        },
    )
    server_id = created.json()["data"]["id"]

    resp = await client.delete(f"/api/servers/{server_id}", headers=_auth(token))
    assert resp.status_code == 200

    # 审计表应有对应删除记录
    db: Database = app.state.db
    async with db.session() as session:
        rows = await AuditService(session).search(action="server.delete")
    assert any(r.target == f"server:{server_id}" for r in rows)
    assert all(r.result == AuditResult.SUCCESS for r in rows)


async def test_add_server_requires_auth(app_client):
    client, _, _ = app_client
    resp = await client.post(
        "/api/servers",
        json={
            "name": "web-05",
            "host": "10.0.0.14",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
        },
    )
    assert resp.status_code == 401


async def test_developer_forbidden_to_add_server_403(app_client):
    client, _, _ = app_client
    token = await _token(client, "dev", "dev-pw")
    resp = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-06",
            "host": "10.0.0.15",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
        },
    )
    assert resp.status_code == 403
