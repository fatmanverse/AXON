"""T0.4 验收:登录发 JWT;未授权 401、越权 403;prod 删除类要求授权角色。"""

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import require_permission
from app.core.config import Settings
from app.core.db import Database
from app.core.permissions import parse_permission
from app.main import create_app
from app.models.base import Base
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-auth-at-least-32-bytes",
        auth_max_failed_attempts=3,
        auth_lockout_minutes=15,
    )
    app: FastAPI = create_app(settings)

    # 探针路由:要求 service:prod:delete 权限
    probe = APIRouter()

    @probe.delete("/_probe/prod-delete")
    async def _prod_delete(
        _=Depends(require_permission(parse_permission("service:prod:delete"))),
    ) -> dict:
        return {"deleted": True}

    app.include_router(probe)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            # 建表 + 播种用户
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                svc = AuthService(session, settings)
                await svc.create_user("admin", "admin-pw", roles=["admin"])
                await svc.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings


async def _login(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp


@pytest.mark.asyncio
async def test_login_returns_jwt(app_client):
    client, _ = app_client
    resp = await _login(client, "admin", "admin-pw")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["access_token"]


@pytest.mark.asyncio
async def test_login_bad_password_401(app_client):
    client, _ = app_client
    resp = await _login(client, "admin", "nope")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_locks_account_after_repeated_failures(app_client):
    client, _ = app_client

    for _ in range(2):
        assert (await _login(client, "admin", "nope")).status_code == 401
    locked = await _login(client, "admin", "nope")
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "account_locked"

    correct_password = await _login(client, "admin", "admin-pw")
    assert correct_password.status_code == 423


@pytest.mark.asyncio
async def test_unauthenticated_request_401(app_client):
    client, _ = app_client
    resp = await client.delete("/_probe/prod-delete")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_prod_delete(app_client):
    client, _ = app_client
    token = (await _login(client, "admin", "admin-pw")).json()["data"]["access_token"]
    resp = await client.delete("/_probe/prod-delete", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_developer_forbidden_prod_delete_403(app_client):
    client, _ = app_client
    token = (await _login(client, "dev", "dev-pw")).json()["data"]["access_token"]
    resp = await client.delete("/_probe/prod-delete", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_change_password_revokes_existing_token(app_client):
    client, _ = app_client
    token = (await _login(client, "dev", "dev-pw")).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    changed = await client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "dev-pw", "new_password": "new-dev-password"},
    )
    assert changed.status_code == 200
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert (await _login(client, "dev", "dev-pw")).status_code == 401
    assert (await _login(client, "dev", "new-dev-password")).status_code == 200


@pytest.mark.asyncio
async def test_logout_revokes_current_token(app_client):
    client, _ = app_client
    token = (await _login(client, "dev", "dev-pw")).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    logged_out = await client.post("/api/auth/logout", headers=headers)
    assert logged_out.status_code == 200
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
