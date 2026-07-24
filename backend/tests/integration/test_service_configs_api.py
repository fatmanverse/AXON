"""配置版本 API 验收(设计 §12 / §15.3)。

覆盖 /api/services/{id}/configs 一组端点(前端 ConfigsPage 依赖):
- POST 新建版本(operator 放行,developer 在 prod 被 403;按 service.env 鉴权 operate)。
- GET 列版本历史(最新在前,标记 is_current)。
- GET current 取当前生效版本;无版本时返回 null。
- POST {version}/activate 切换生效版(配置回滚)。
- 未认证 401;服务不存在 404。

用内存 sqlite;鉴权按 service:{env}:operate 动态判定(与生命周期一致)。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.schemas.environment import EnvironmentCreate
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository


@pytest_asyncio.fixture
async def app_client(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-configs-api-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
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


async def test_create_and_list_config_versions(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)

    r1 = await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=1", "format": "env", "comment": "初版"},
    )
    assert r1.status_code == 201
    v1 = r1.json()["data"]
    assert v1["version"] == 1
    assert v1["is_current"] is True
    assert v1["content"] == "A=1"
    assert v1["format"] == "env"

    r2 = await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=2", "format": "env"},
    )
    assert r2.status_code == 201
    assert r2.json()["data"]["version"] == 2

    listing = await client.get(f"/api/services/{service_id}/configs", headers=_auth(token))
    assert listing.status_code == 200
    rows = listing.json()["data"]
    # 最新在前
    assert [row["version"] for row in rows] == [2, 1]
    # 仅新版是 current
    current_flags = {row["version"]: row["is_current"] for row in rows}
    assert current_flags == {2: True, 1: False}


async def test_get_current_returns_latest(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)
    await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=1"},
    )

    resp = await client.get(f"/api/services/{service_id}/configs/current", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 1


async def test_get_current_null_when_no_versions(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)

    resp = await client.get(f"/api/services/{service_id}/configs/current", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["data"] is None


async def test_activate_switches_current(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)
    await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=1"},
    )
    await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(token),
        json={"content": "A=2"},
    )

    # 切回 v1(配置回滚)
    resp = await client.post(f"/api/services/{service_id}/configs/1/activate", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 1
    assert resp.json()["data"]["is_current"] is True

    current = await client.get(f"/api/services/{service_id}/configs/current", headers=_auth(token))
    assert current.json()["data"]["version"] == 1


async def test_activate_missing_version_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token)

    resp = await client.post(
        f"/api/services/{service_id}/configs/99/activate", headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_developer_forbidden_to_write_prod_config(app_client):
    client, _, _ = app_client
    op_token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op_token, name="prod-svc", env="prod")

    dev_token = await _token(client, "dev", "dev-pw")
    resp = await client.post(
        f"/api/services/{service_id}/configs",
        headers=_auth(dev_token),
        json={"content": "A=1"},
    )
    assert resp.status_code == 403


async def test_list_requires_auth(app_client):
    client, _, _ = app_client
    op_token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op_token)

    resp = await client.get(f"/api/services/{service_id}/configs")
    assert resp.status_code == 401


async def test_config_for_unknown_service_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.get(
        "/api/services/does-not-exist-000000000000000/configs",
        headers=_auth(token),
    )
    assert resp.status_code == 404
