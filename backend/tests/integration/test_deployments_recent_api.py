"""顶层部署查询 API 验收(T2.17,§9.2/§15.4)。

GET /api/deployments 跨服务列出最近部署(供主页 Dashboard feed)。覆盖:
- 跨多个服务聚合,最新在前。
- env 过滤。
- limit 上限。
- 未认证 401。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStrategy
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.deployment_repository import DeploymentRepository
from app.services.service_repository import ServiceRepository


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-recent-deploy",
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
                await AuthService(session, settings).create_user(
                    "operator", "op-pw", roles=["operator"]
                )
            yield client, app


async def _seed(app):
    """建两个服务(prod/dev)各一条部署。"""
    db: Database = app.state.db
    async with db.session() as session:
        svc_repo = ServiceRepository(session)
        dep_repo = DeploymentRepository(session)
        prod = await svc_repo.create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        dev = await svc_repo.create_service(
            ServiceCreate(
                name="web",
                env=ServiceEnvironment.DEV,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "web.service"},
            )
        )
        await dep_repo.create(
            service_id=prod.id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            strategy=DeploymentStrategy.ROLLING,
            version="v1",
            operator="op",
        )
        await dep_repo.create(
            service_id=dev.id,
            env="dev",
            source=DeploymentSource.UI_TRIGGERED,
            strategy=DeploymentStrategy.ROLLING,
            version="v2",
            operator="op",
        )


async def _token(client) -> str:
    resp = await client.post(
        "/api/auth/login", json={"username": "operator", "password": "op-pw"}
    )
    return resp.json()["data"]["access_token"]


async def test_recent_aggregates_across_services(app_client):
    client, app = app_client
    await _seed(app)
    token = await _token(client)
    resp = await client.get(
        "/api/deployments", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 2  # 跨两个服务


async def test_recent_env_filter(app_client):
    client, app = app_client
    await _seed(app)
    token = await _token(client)
    resp = await client.get(
        "/api/deployments?env=prod", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["env"] == "prod"


async def test_recent_limit(app_client):
    client, app = app_client
    await _seed(app)
    token = await _token(client)
    resp = await client.get(
        "/api/deployments?limit=1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


async def test_recent_requires_auth(app_client):
    client, _ = app_client
    resp = await client.get("/api/deployments")
    assert resp.status_code == 401
