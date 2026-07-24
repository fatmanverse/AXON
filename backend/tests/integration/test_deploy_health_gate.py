"""部署后健康检查接入验收(T3.8,§11.1)。

证明健康检查已真正接入部署编排(此前 DeploymentService 从未被注入 health_checker,
HealthChecker 是死代码):注入一个「必失败」的 checker,部署后 deployment 落 FAILED、
task 落 failed;注入「必通过」的 checker 则落 SUCCESS。

用 app.state.health_checker 覆写 + fake pipeline,不触真实 CI/网络。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.deployment import DeploymentStatus
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.deployment_repository import DeploymentRepository
from app.services.health_checker import HealthResult
from app.services.service_repository import ServiceRepository


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        return "build-1"

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


class _StubChecker:
    """必失败/必通过的健康检查桩,验证接入生效(不做真实探测)。"""

    def __init__(self, *, healthy: bool) -> None:
        self._healthy = healthy
        self.called = False

    async def check(self, config) -> HealthResult:
        self.called = True
        return HealthResult(healthy=self._healthy, attempts=1, detail="stub")


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-health-gate-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
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
                await AuthService(session, settings).create_user(
                    "operator", "op-pw", roles=["operator"]
                )
            yield client, app


async def _seed_service_with_health(app) -> str:
    db: Database = app.state.db
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
                health_check={"type": "http", "url": "http://x/health"},
            )
        )
        return service.id


async def _token(client) -> str:
    resp = await client.post("/api/auth/login", json={"username": "operator", "password": "op-pw"})
    return resp.json()["data"]["access_token"]


async def _latest_status(app, service_id) -> DeploymentStatus:
    db: Database = app.state.db
    async with db.session() as session:
        deployments = await DeploymentRepository(session).list_for_service(service_id, env="prod")
    return deployments[0].status


async def test_failing_health_check_marks_deploy_failed(app_client):
    client, app = app_client
    checker = _StubChecker(healthy=False)
    app.state.health_checker = checker
    service_id = await _seed_service_with_health(app)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {await _token(client)}"},
        json={"version": "v1", "strategy": "rolling"},
    )
    assert resp.status_code == 202

    assert checker.called, "健康检查应被调用(证明已接入)"
    assert await _latest_status(app, service_id) == DeploymentStatus.FAILED


async def test_passing_health_check_marks_deploy_success(app_client):
    client, app = app_client
    checker = _StubChecker(healthy=True)
    app.state.health_checker = checker
    service_id = await _seed_service_with_health(app)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {await _token(client)}"},
        json={"version": "v1", "strategy": "rolling"},
    )
    assert resp.status_code == 202
    assert checker.called
    assert await _latest_status(app, service_id) == DeploymentStatus.SUCCESS
