"""T2.5 回滚 API 验收(设计 §11.1/§15.2)。

POST /api/services/{id}/rollback:重部署上一版闭环。用 fake pipeline adapter,
按 service.env 动态鉴权(与 deploy 同权限点),BackgroundTasks 在响应前跑完。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.deployment_repository import DeploymentRepository
from app.services.service_repository import ServiceRepository


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref, *, params):
        return "rb-1"

    async def get_status(self, ref, *, run_id):
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref, *, run_id):
        return "log"


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-rollback",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        # 本用例验证直接回滚路径(非审批);prod 审批门控由 test_approval_flow_api 覆盖。
        require_prod_approval=False,
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            app.state.pipeline_adapter_provider = lambda _svc: _FakeAdapter()
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                auth = AuthService(session, settings)
                await auth.create_user("operator", "op-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings, app


async def _seed_service(app, *, env=ServiceEnvironment.PROD) -> str:
    db: Database = app.state.db
    async with db.session() as session:
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return svc.id


async def _seed_success(app, service_id, *, version, artifact) -> None:
    db: Database = app.state.db
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version=version,
            artifact=artifact,
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_rollback_returns_task_and_succeeds(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    await _seed_success(app, service_id, version="v1", artifact="registry/app:v1")
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(f"/api/services/{service_id}/rollback", headers=_auth(token))
    assert resp.status_code == 202
    task_id = resp.json()["data"]["task_id"]

    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert got.json()["data"]["status"] == "success"


async def test_rollback_forbidden_for_developer_on_prod(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)
    await _seed_success(app, service_id, version="v1", artifact="a1")
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(f"/api/services/{service_id}/rollback", headers=_auth(token))
    assert resp.status_code == 403


async def test_rollback_requires_auth(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    resp = await client.post(f"/api/services/{service_id}/rollback")
    assert resp.status_code == 401


async def test_rollback_unknown_service_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    resp = await client.post("/api/services/" + "0" * 32 + "/rollback", headers=_auth(token))
    assert resp.status_code == 404
