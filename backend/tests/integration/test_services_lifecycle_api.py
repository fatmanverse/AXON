"""T1.10 服务生命周期 API 验收(设计 §15.2 / §10.2)。

覆盖:
- start/stop/restart 返回 202 + task_id;后台异步执行后 task 落 success。
- delete 走高危授权:operator 放行、developer 在 prod 被 403。
- 生命周期动作按 service.env 动态鉴权(prod 严格)。
- 未认证 401、服务不存在 404。
- 写审计:每次动作在审计表留痕。

用 fake connector 注入,不触真实 SSH;BackgroundTasks 在 ASGITransport 下于
响应返回前执行完,故可直接断言终态。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters import ssh_executor
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository

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
        return _FakeProcess(exit_status=1, stdout="", stderr="boom")

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-lifecycle",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
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
                auth = AuthService(session, settings)
                await auth.create_user("admin", "admin-pw", roles=["admin"])
                await auth.create_user("operator", "op-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings, app


async def _seed_service(app, *, env=ServiceEnvironment.PROD, runtime=Runtime.SYSTEMD) -> str:
    """建一台 SSH 服务器 + systemd 服务 + 放置,返回 service_id。"""
    db: Database = app.state.db
    store = app.state.secret_store
    async with db.session() as session:
        cred_id = store.put("ssh-key", _FAKE_KEY)
        server = await ServerRepository(session).create(
            ServerCreate(
                name="host-life",
                host="10.0.0.40",
                access_mode=AccessMode.SSH,
                ssh_credential_id=cred_id,
                labels={"ssh_username": "ops", "ssh_port": 22},
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=runtime,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        await svc_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=server.id)
        )
        return service.id


async def _token(client, username, password):
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_restart_returns_task_id_and_marks_success(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(f"/api/services/{service_id}/restart", headers=_auth(token))

    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    task_id = body["data"]["task_id"]
    assert task_id

    # BackgroundTasks 已于响应前跑完,查任务应为 success
    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["data"]["status"] == "success"


async def test_start_and_stop_accepted(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.DEV)
    token = await _token(client, "dev", "dev-pw")

    for action in ("start", "stop"):
        resp = await client.post(f"/api/services/{service_id}/{action}", headers=_auth(token))
        assert resp.status_code == 202
        assert resp.json()["data"]["task_id"]


async def test_delete_requires_operator_and_developer_forbidden_on_prod(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)

    dev_token = await _token(client, "dev", "dev-pw")
    forbidden = await client.delete(f"/api/services/{service_id}", headers=_auth(dev_token))
    assert forbidden.status_code == 403

    op_token = await _token(client, "operator", "op-pw")
    allowed = await client.delete(f"/api/services/{service_id}", headers=_auth(op_token))
    assert allowed.status_code == 202
    assert allowed.json()["data"]["task_id"]


async def test_developer_forbidden_to_restart_prod(app_client):
    """prod 严格:developer 只有 service:prod:read,无 operate 权限 → 403。"""
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(f"/api/services/{service_id}/restart", headers=_auth(token))
    assert resp.status_code == 403


async def test_lifecycle_requires_auth(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    resp = await client.post(f"/api/services/{service_id}/restart")
    assert resp.status_code == 401


async def test_unknown_service_returns_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    resp = await client.post(
        "/api/services/" + "0" * 32 + "/restart", headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_restart_writes_audit(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "admin", "admin-pw")

    await client.post(f"/api/services/{service_id}/restart", headers=_auth(token))

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AuditService(session).search(action="service.restart")
    assert any(r.target == f"service:{service_id}" for r in rows)
