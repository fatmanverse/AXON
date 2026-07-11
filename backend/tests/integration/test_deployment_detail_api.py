"""T3.4 全链路关联:部署详情内嵌扫描结论(§7.2/§14.8)。

GET /api/services/{id}/deployments/{deployment_id} 返回部署记录 + 按 git_sha
关联的 scan_results 列表,实现"点开一次部署看到这次上线扫描过没有、有没有高危"。
向前可追溯到扫描与提交。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.deployment import DeploymentSource
from app.models.scan_result import Scanner
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.deployment_repository import DeploymentRepository
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_repository import ServiceRepository


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-detail",
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
            yield client, settings, app


async def _seed(app, *, git_sha, critical):
    db: Database = app.state.db
    async with db.session() as session:
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing", env=ServiceEnvironment.DEV,
                runtime=Runtime.SYSTEMD, runtime_ref={"unit_name": "billing.service"},
            )
        )
        service_id = svc.id
        dep = await DeploymentRepository(session).create(
            service_id=service_id, env="dev", source=DeploymentSource.UI_TRIGGERED,
            version="v1", git_sha=git_sha,
        )
        deployment_id = dep.id
        await ScanResultRepository(session).upsert(
            service="billing", git_sha=git_sha, scanner=Scanner.SONARQUBE,
            critical=critical, high=1, passed=(critical == 0),
        )
    return service_id, deployment_id


async def _token(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "operator", "password": "op-pw"}
    )
    return resp.json()["data"]["access_token"]


async def test_detail_includes_linked_scans(app_client):
    client, _, app = app_client
    service_id, deployment_id = await _seed(app, git_sha="sha1", critical=2)
    token = await _token(client)

    resp = await client.get(
        f"/api/services/{service_id}/deployments/{deployment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["deployment"]["version"] == "v1"
    assert data["deployment"]["git_sha"] == "sha1"
    # 关联扫描结论
    assert len(data["scans"]) == 1
    assert data["scans"][0]["scanner"] == "sonarqube"
    assert data["scans"][0]["critical"] == 2


async def test_detail_empty_scans_when_no_git_sha(app_client):
    client, _, app = app_client
    db: Database = app.state.db
    async with db.session() as session:
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="orders", env=ServiceEnvironment.DEV,
                runtime=Runtime.SYSTEMD, runtime_ref={"unit_name": "orders.service"},
            )
        )
        service_id = svc.id
        dep = await DeploymentRepository(session).create(
            service_id=service_id, env="dev", source=DeploymentSource.UI_TRIGGERED,
            version="v1",
        )
        deployment_id = dep.id
    token = await _token(client)

    resp = await client.get(
        f"/api/services/{service_id}/deployments/{deployment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["scans"] == []


async def test_detail_unknown_deployment_404(app_client):
    client, _, app = app_client
    service_id, _ = await _seed(app, git_sha="sha1", critical=0)
    token = await _token(client)
    resp = await client.get(
        f"/api/services/{service_id}/deployments/{'0' * 32}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_detail_requires_auth(app_client):
    client, _, app = app_client
    service_id, deployment_id = await _seed(app, git_sha="sha1", critical=0)
    resp = await client.get(
        f"/api/services/{service_id}/deployments/{deployment_id}"
    )
    assert resp.status_code == 401
