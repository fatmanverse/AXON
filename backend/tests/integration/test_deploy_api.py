"""T2.3 UI 触发部署 API 验收(设计 §15.2 / §8.1 模式 A)。

覆盖:
- POST /api/services/{id}/deploy 落 deployment(running→success)并返回 task_id;
  注入 fake pipeline adapter,不触真实 CI。
- deploy 动作按 service.env 动态鉴权:operator 放行、developer 在 prod 被 403。
- 未认证 401、服务不存在 404。
- 部署后 GET /api/services/{id}/deployments 能查到该记录。

BackgroundTasks 在 ASGITransport 下于响应返回前执行完,故可直接断言终态。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskType
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeAdapter(PipelineAdapter):
    """记录 trigger 调用的假 CI 适配器。"""

    def __init__(self) -> None:
        self.triggered: list[dict] = []

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        self.triggered.append({"ref": ref, "params": params})
        return "build-1"

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-deploy-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        # 本文件验证「部署编排本身」,不测审批;显式关审批走直接部署路径
        require_prod_approval=False,
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            # 注入 fake pipeline adapter provider,避免真实 CI
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
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return service.id


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_deploy_returns_task_and_marks_success(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1.2.0", "strategy": "rolling"},
    )

    assert resp.status_code == 202
    task_id = resp.json()["data"]["task_id"]
    assert task_id

    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert got.json()["data"]["status"] == "success"


async def test_deploy_creates_deployment_record(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v2.0.0"},
    )

    resp = await client.get(f"/api/services/{service_id}/deployments", headers=_auth(token))
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["version"] == "v2.0.0"
    assert rows[0]["source"] == "ui-triggered"
    assert rows[0]["status"] == "success"


async def test_developer_forbidden_to_deploy_prod(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1.0"},
    )
    assert resp.status_code == 403


async def test_developer_can_deploy_dev(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.DEV)
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1.0"},
    )
    assert resp.status_code == 202


async def test_deploy_requires_auth(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    resp = await client.post(f"/api/services/{service_id}/deploy", json={"version": "v1.0"})
    assert resp.status_code == 401


async def test_deploy_unknown_service_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    resp = await client.post(
        "/api/services/" + "0" * 32 + "/deploy",
        headers=_auth(token),
        json={"version": "v1.0"},
    )
    assert resp.status_code == 404


async def test_deploy_rejects_when_service_has_active_deployment_operation(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.DEV)
    token = await _token(client, "operator", "op-pw")
    async with app.state.db.session() as session:
        active = await TaskRepository(session).create_deployment_operation(
            type=TaskType.ROLLBACK,
            service_id=service_id,
            created_by="other-operator",
        )

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v3.0.0"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "deployment_in_progress",
        "message": "该服务已有部署或回滚任务正在执行",
        "details": {"active_task_id": active.id},
    }
