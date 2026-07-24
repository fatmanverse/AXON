"""环境管理 API 验收(自定义环境管理)。

覆盖:
- 创建环境:写审计、requires_approval 落库、响应视图。
- 列表(按 name 排序)。
- 删除走鉴权 + 写审计。
- 重名 409、未授权 401、无权限 403。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.audit import AuditResult
from app.models.base import Base
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-environments-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
    )
    app: FastAPI = create_app(settings)
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


async def test_create_environment_writes_audit(app_client):
    client, _, app = app_client
    token = await _token(client, "admin", "admin-pw")

    resp = await client.post(
        "/api/environments",
        headers=_auth(token),
        json={
            "name": "prod",
            "display_name": "生产",
            "requires_approval": True,
            "description": "生产环境",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["name"] == "prod"
    assert data["requires_approval"] is True

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AuditService(session).search(action="environment.create")
    assert any(r.target == f"environment:{data['id']}" for r in rows)
    assert all(r.result == AuditResult.SUCCESS for r in rows)


async def test_list_environments_sorted(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    for name in ("staging", "dev"):
        await client.post("/api/environments", headers=_auth(token), json={"name": name})

    resp = await client.get("/api/environments", headers=_auth(token))
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["data"]]
    assert names == ["dev", "staging"]


async def test_duplicate_name_conflict(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    await client.post("/api/environments", headers=_auth(token), json={"name": "dev"})
    resp = await client.post("/api/environments", headers=_auth(token), json={"name": "dev"})
    assert resp.status_code == 409


async def test_delete_environment_writes_audit(app_client):
    client, _, app = app_client
    token = await _token(client, "admin", "admin-pw")
    created = await client.post("/api/environments", headers=_auth(token), json={"name": "temp"})
    env_id = created.json()["data"]["id"]

    resp = await client.delete(f"/api/environments/{env_id}", headers=_auth(token))
    assert resp.status_code == 200

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AuditService(session).search(action="environment.delete")
    assert any(r.target == f"environment:{env_id}" for r in rows)


async def test_create_requires_auth(app_client):
    client, _, _ = app_client
    resp = await client.post("/api/environments", json={"name": "x"})
    assert resp.status_code == 401


async def test_developer_forbidden_to_create_403(app_client):
    client, _, _ = app_client
    token = await _token(client, "dev", "dev-pw")
    resp = await client.post("/api/environments", headers=_auth(token), json={"name": "x"})
    assert resp.status_code == 403


async def test_developer_can_list(app_client):
    client, _, _ = app_client
    admin = await _token(client, "admin", "admin-pw")
    await client.post("/api/environments", headers=_auth(admin), json={"name": "dev"})
    dev = await _token(client, "dev", "dev-pw")
    resp = await client.get("/api/environments", headers=_auth(dev))
    assert resp.status_code == 200
    assert [e["name"] for e in resp.json()["data"]] == ["dev"]
