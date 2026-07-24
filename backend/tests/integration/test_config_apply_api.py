"""配置下发 API 验收(T2.12,设计 §15.3)。

覆盖:
- POST /api/services/{id}/configs/{v}/apply 触发下发,落 update_config task,返回 202+task_id。
- GET  /api/services/{id}/configs/{v}/deliveries 查看逐目标下发结果。
- 新建配置版本可带 target_path。
- 下发按 service.env 鉴权 operate;developer 在 prod 被 403。
- 目标版本不存在 404;未认证 401。

注入 fake ssh connector,不触真实 SSH;下发经 BackgroundTasks 同步执行(测试内可断言结果)。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.server import AccessMode, Server
from app.models.service import ServicePlacement
from app.schemas.environment import EnvironmentCreate
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository


class _FakeProc:
    def __init__(self) -> None:
        self.exit_status = 0
        self.stdout = "ok"
        self.stderr = ""


class _FakeConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, command, timeout=None):
        return _FakeProc()


def _fake_connector(**kwargs):
    return _FakeConn()


@pytest_asyncio.fixture
async def app_client(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-config-apply-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            app.state.ssh_connector = _fake_connector
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                auth = AuthService(session, settings)
                await auth.create_user("operator", "op-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
                # 建服务须归属已存在的环境(§10.1 软校验);seed 标准三环境供各用例使用
                env_repo = EnvironmentRepository(session)
                await env_repo.create(EnvironmentCreate(name="dev"))
                await env_repo.create(EnvironmentCreate(name="staging"))
                await env_repo.create(EnvironmentCreate(name="prod"))
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_service(client, token, name="billing", env="dev", runtime="systemd"):
    resp = await client.post(
        "/api/services",
        headers=_auth(token),
        json={
            "name": name,
            "env": env,
            "runtime": runtime,
            "runtime_ref": {"unit_name": f"{name}.service"},
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


async def _add_placement(app, service_id):
    """给服务挂一个落在 SSH server 上的放置点。凭证存入保险箱供 SSHExecutor 建连。"""
    db: Database = app.state.db
    cred_id = app.state.secret_store.put("ssh-key", "-----FAKE KEY-----")
    async with db.session() as session:
        server = Server(
            name="host-a",
            host="10.0.0.1",
            access_mode=AccessMode.SSH,
            ssh_credential_id=cred_id,
        )
        session.add(server)
        await session.flush()
        session.add(ServicePlacement(service_id=service_id, server_id=server.id))


async def test_create_config_with_target_path(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)

    resp = await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=1", "target_path": "/etc/app/app.env"},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["target_path"] == "/etc/app/app.env"


async def test_apply_delivers_and_records(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)
    await _add_placement(app, service_id)
    await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=1", "target_path": "/etc/app/app.env"},
    )

    resp = await client.post(f"/api/services/{service_id}/configs/1/apply", headers=_auth(token))
    assert resp.status_code == 202
    assert resp.json()["data"]["task_id"]

    deliveries = await client.get(
        f"/api/services/{service_id}/configs/1/deliveries", headers=_auth(token)
    )
    assert deliveries.status_code == 200
    rows = deliveries.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == "success"


async def test_apply_missing_version_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)

    resp = await client.post(f"/api/services/{service_id}/configs/9/apply", headers=_auth(token))
    assert resp.status_code == 404


async def test_apply_forbidden_for_developer_in_prod(app_client):
    client, _, _ = app_client
    op_token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op_token, name="prod-svc", env="prod")
    await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(op_token),
        json={"content": "A=1", "target_path": "/etc/p.env"},
    )

    dev_token = await _token(client, "dev", "dev-pw")
    resp = await client.post(
        f"/api/services/{service_id}/configs/1/apply", headers=_auth(dev_token)
    )
    assert resp.status_code == 403


async def test_apply_requires_auth(app_client):
    client, _, _ = app_client
    op_token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op_token)

    resp = await client.post(f"/api/services/{service_id}/configs/1/apply")
    assert resp.status_code == 401
