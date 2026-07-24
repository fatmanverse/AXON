"""install-agent 端点集成测试(需求4:Agent 经 SSH 下发)。

对 SSH 纳管的服务器,经 SSH 下发安装 axon-agent:落 agent_install task,
后台经 SSHExecutor 跑安装脚本,前端轮询 task 终态。注入 fake connector,
不触真实 SSH/下载。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters import ssh_executor
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.schemas.environment import EnvironmentCreate
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


class _FakeProcess:
    def __init__(self, exit_status=0, stdout="", stderr=""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    def __init__(self, *, ok=True):
        self._ok = ok
        self.commands = []

    async def run(self, command, *, timeout=None):
        self.commands.append(command)
        if self._ok:
            return _FakeProcess(exit_status=0, stdout="done")
        return _FakeProcess(exit_status=1, stderr="fail")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


@pytest_asyncio.fixture
async def app_client(monkeypatch, tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-install-agent-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
        control_plane_base_url="http://cp:8000",
        agent_insecure_install=True,
    )
    app: FastAPI = create_app(settings)
    monkeypatch.setattr(ssh_executor, "_default_connector", lambda **_: _FakeConnection(ok=True))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                await AuthService(session, settings).create_user(
                    "admin", "admin-pw", roles=["admin"]
                )
                await EnvironmentRepository(session).create(
                    EnvironmentCreate(name="dev", display_name="开发", requires_approval=False)
                )
            yield client, settings, app


async def _token(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"})
    return resp.json()["data"]["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _register_ssh_server(client, token):
    resp = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": "web-agent-01",
            "host": "10.5.0.10",
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
            "environment": "dev",
        },
    )
    return resp.json()["data"]["id"]


async def test_install_agent_returns_task_and_succeeds(app_client):
    client, _, _ = app_client
    token = await _token(client)
    server_id = await _register_ssh_server(client, token)

    resp = await client.post(f"/api/servers/{server_id}/install-agent", headers=_auth(token))
    assert resp.status_code == 202
    task_id = resp.json()["data"]["task_id"]
    assert task_id

    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert got.json()["data"]["status"] == "success"


async def test_install_agent_rejects_non_ssh_server(app_client):
    client, _, app = app_client
    token = await _token(client)
    # 直接建一个 agent 模式服务器
    from app.models.server import AccessMode
    from app.schemas.server import ServerCreate
    from app.services.server_repository import ServerRepository

    db: Database = app.state.db
    async with db.session() as session:
        created = await ServerRepository(session).create(
            ServerCreate(
                name="agent-node-x",
                host="10.5.0.99",
                access_mode=AccessMode.AGENT,
                environment="dev",
                agent_id="agent-xyz",
            )
        )
        agent_server_id = created.id

    resp = await client.post(f"/api/servers/{agent_server_id}/install-agent", headers=_auth(token))
    assert resp.status_code == 400


async def test_install_agent_requires_permission(app_client):
    client, settings, app = app_client
    token = await _token(client)
    server_id = await _register_ssh_server(client, token)

    # viewer 无 server:*:write 权限
    db: Database = app.state.db
    async with db.session() as session:
        await AuthService(session, settings).create_user("viewer", "v-pw", roles=["viewer"])
    vresp = await client.post("/api/auth/login", json={"username": "viewer", "password": "v-pw"})
    vtoken = vresp.json()["data"]["access_token"]

    resp = await client.post(f"/api/servers/{server_id}/install-agent", headers=_auth(vtoken))
    assert resp.status_code == 403
