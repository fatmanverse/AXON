"""T1.17 前置:services 列表/创建 API 验收(设计 §15.4 / §15.2)。

覆盖:
- POST /api/services 创建服务(operator 放行,developer 在 prod 被 403)。
- GET /api/services 列表返回服务视图(含 placements 计数)。
- GET /api/services?env=prod&runtime=systemd 按环境/运行时过滤。
- 未认证 401。

用 fake connector 注入,不触真实 SSH;鉴权按 service:{env}:{action} 动态判定。
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
        jwt_secret="itest-secret-services-query-at-least-32-bytes",
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
                await auth.create_user("admin", "admin-pw", roles=["admin"])
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


def _service_body(name="billing", env="prod", runtime="systemd") -> dict:
    return {
        "name": name,
        "env": env,
        "runtime": runtime,
        "runtime_ref": {"unit_name": f"{name}.service"},
    }


async def test_create_service_returns_view(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.post("/api/services", headers=_auth(token), json=_service_body())

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["name"] == "billing"
    assert data["env"] == "prod"
    assert data["runtime"] == "systemd"
    assert data["id"]
    assert data["placement_count"] == 0


async def test_list_services_shows_created(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    await client.post("/api/services", headers=_auth(token), json=_service_body("svc-a"))
    await client.post(
        "/api/services",
        headers=_auth(token),
        json=_service_body("svc-b", env="dev", runtime="docker"),
    )

    resp = await client.get("/api/services", headers=_auth(token))

    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()["data"]}
    assert {"svc-a", "svc-b"} <= names


async def test_list_services_filters_by_env_and_runtime(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    await client.post(
        "/api/services", headers=_auth(token), json=_service_body("prod-sysd", env="prod")
    )
    await client.post(
        "/api/services",
        headers=_auth(token),
        json=_service_body("dev-docker", env="dev", runtime="docker"),
    )

    resp = await client.get("/api/services", headers=_auth(token), params={"env": "prod"})
    assert {s["name"] for s in resp.json()["data"]} == {"prod-sysd"}

    resp = await client.get("/api/services", headers=_auth(token), params={"runtime": "docker"})
    assert {s["name"] for s in resp.json()["data"]} == {"dev-docker"}


async def test_developer_forbidden_to_create_prod_service(app_client):
    client, _, _ = app_client
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post("/api/services", headers=_auth(token), json=_service_body(env="prod"))
    assert resp.status_code == 403


async def test_developer_can_create_dev_service(app_client):
    client, _, _ = app_client
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(
        "/api/services",
        headers=_auth(token),
        json=_service_body("dev-svc", env="dev"),
    )
    assert resp.status_code == 201


async def test_list_requires_auth(app_client):
    client, _, _ = app_client
    resp = await client.get("/api/services")
    assert resp.status_code == 401
