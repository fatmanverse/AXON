"""生产审批流 API 验收(T2.15,设计 §10.2/§13)。

覆盖:
- require_prod_approval 开启时,prod deploy 不直接执行,落 pending 审批(返回 approval)。
- dev/staging deploy 不受审批影响,仍直接落 task(202)。
- GET /api/approvals 列待审批(operator 有 approve 权限)。
- POST /api/approvals/{id}/approve:批准后建 task 并回填 task_id。
- POST /api/approvals/{id}/reject:拒绝落 rejected,不建 task。
- 无 approve 权限者(developer)审批被 403。
- 重复决策 409。

注入 fake pipeline provider,不触真实 CI;prod 审批开关在 fixture 打开。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.services.auth_service import AuthService


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        return "run-approval"

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


@pytest_asyncio.fixture
async def app_client(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-approval-flow",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
        require_prod_approval=True,
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
                # 第二个审批人:自审批防护要求批准人与发起人不同(§13)
                await auth.create_user("boss", "boss-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_service(client, token, name, env):
    resp = await client.post(
        "/api/services",
        headers=_auth(token),
        json={
            "name": name,
            "env": env,
            "runtime": "systemd",
            "runtime_ref": {"unit_name": f"{name}.service"},
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


async def test_prod_deploy_creates_pending_approval(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token, "billing", "prod")

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    # prod 审批开启:返回 202 但体是审批(pending_approval),不是 task
    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["pending_approval"] is True
    assert data["approval_id"]


async def test_dev_deploy_bypasses_approval(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token, "billing-dev", "dev")

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    assert resp.status_code == 202
    data = resp.json()["data"]
    # dev 不走审批:直接返回 task
    assert data.get("pending_approval") is not True
    assert data["task_id"]


async def test_approve_creates_task(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token, "billing", "prod")
    deploy = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    approval_id = deploy.json()["data"]["approval_id"]

    listing = await client.get("/api/approvals", headers=_auth(token))
    assert listing.status_code == 200
    assert any(a["id"] == approval_id for a in listing.json()["data"])

    # 审批人须独立于发起人(§13):由第二个 operator 批准
    boss_token = await _token(client, "boss", "boss-pw")
    approve = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(boss_token))
    assert approve.status_code == 202
    data = approve.json()["data"]
    assert data["status"] == "approved"
    assert data["task_id"]


async def test_reject_closes_without_task(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token, "billing", "prod")
    deploy = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    approval_id = deploy.json()["data"]["approval_id"]

    boss_token = await _token(client, "boss", "boss-pw")
    reject = await client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=_auth(boss_token),
        json={"reason": "生产窗口未到"},
    )
    assert reject.status_code == 200
    data = reject.json()["data"]
    assert data["status"] == "rejected"
    assert data["task_id"] is None


async def test_developer_cannot_approve(app_client):
    client, _, _ = app_client
    op_token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op_token, "billing", "prod")
    deploy = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(op_token),
        json={"version": "v1", "strategy": "rolling"},
    )
    approval_id = deploy.json()["data"]["approval_id"]

    dev_token = await _token(client, "dev", "dev-pw")
    resp = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(dev_token))
    assert resp.status_code == 403


async def test_cannot_approve_twice(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    boss = await _token(client, "boss", "boss-pw")
    service_id = await _create_service(client, token, "billing", "prod")
    deploy = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    approval_id = deploy.json()["data"]["approval_id"]
    await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(boss))

    resp = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(boss))
    assert resp.status_code == 409


async def test_cannot_approve_own_request(app_client):
    """自审批防护(§13):发起人不能批准自己发起的操作。"""
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, token, "billing", "prod")
    deploy = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1", "strategy": "rolling"},
    )
    approval_id = deploy.json()["data"]["approval_id"]

    resp = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(token))
    assert resp.status_code == 403


async def _seed_success_deployment(app, service_id):
    """给服务留一版成功部署,供 rollback 取制品。"""
    from app.models.deployment import DeploymentSource, DeploymentStatus
    from app.services.deployment_repository import DeploymentRepository

    db = app.state.db
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version="v1",
            artifact="registry/app:v1",
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)


async def test_prod_rollback_creates_pending_approval(app_client):
    """prod rollback 在审批开关开启时不直接执行,落 pending 审批(HIGH-2)。"""
    client, _, app = app_client
    op = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op, "billing", "prod")
    await _seed_success_deployment(app, service_id)

    resp = await client.post(f"/api/services/{service_id}/rollback", headers=_auth(op))
    assert resp.status_code == 202
    body = resp.json()["data"]
    assert body["pending_approval"] is True
    assert body["approval_id"]
    # 未直接建 rollback task(返回的是审批而非 task_id)
    assert "task_id" not in body


async def test_prod_delete_creates_pending_approval(app_client):
    """prod delete 在审批开关开启时不直接执行,落 pending 审批(HIGH-2)。"""
    client, _, app = app_client
    op = await _token(client, "operator", "op-pw")
    service_id = await _create_service(client, op, "gateway", "prod")

    resp = await client.delete(f"/api/services/{service_id}", headers=_auth(op))
    assert resp.status_code == 202
    body = resp.json()["data"]
    assert body["pending_approval"] is True
    assert body["approval_id"]


async def test_approve_rollback_dispatches_execution(app_client):
    """批准 rollback 审批后,经 DeploymentService 执行回滚并落 task(HIGH-2)。"""
    client, _, app = app_client
    op = await _token(client, "operator", "op-pw")
    boss = await _token(client, "boss", "boss-pw")
    service_id = await _create_service(client, op, "billing", "prod")
    await _seed_success_deployment(app, service_id)

    pending = await client.post(f"/api/services/{service_id}/rollback", headers=_auth(op))
    approval_id = pending.json()["data"]["approval_id"]

    # 由另一审批人批准(自审批防护)
    approved = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(boss))
    assert approved.status_code == 202
    task_id = approved.json()["data"]["task_id"]
    assert task_id
    # 回滚 task 跑到成功(fake CI 立即成功)
    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(op))
    assert got.json()["data"]["status"] == "success"


async def test_approve_delete_dispatches_lifecycle(app_client):
    """批准 delete 审批后,经 LifecycleService 执行删除并落 task(HIGH-2)。

    删除动作在无真实 SSH/agent 连接时会落 failed(agent 占位/无 executor),
    但关键是走了执行路径并建出 delete task,而非被 501 拒绝。"""
    client, _, app = app_client
    op = await _token(client, "operator", "op-pw")
    boss = await _token(client, "boss", "boss-pw")
    service_id = await _create_service(client, op, "gateway", "prod")

    pending = await client.delete(f"/api/services/{service_id}", headers=_auth(op))
    approval_id = pending.json()["data"]["approval_id"]

    approved = await client.post(f"/api/approvals/{approval_id}/approve", headers=_auth(boss))
    assert approved.status_code == 202
    task_id = approved.json()["data"]["task_id"]
    assert task_id
    # 建出的是 delete task(执行路径已接通,不再 501)
    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(op))
    assert got.json()["data"]["type"] == "delete"
