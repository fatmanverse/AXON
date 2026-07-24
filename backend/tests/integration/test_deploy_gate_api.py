"""T3.3 部署质量门禁 端到端验收(§7.2)。

deploy 端点在触发前查 scan_results:带 git_sha 且存在 critical 时返回 422 拦截,
不建 task、不触发 CI;无 critical 或策略关闭时正常 202。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.scan_result import Scanner
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_repository import ServiceRepository


class _FakeAdapter(PipelineAdapter):
    def __init__(self):
        self.triggered = []

    async def trigger(self, ref, *, params):
        self.triggered.append(params)
        return "run-1"

    async def get_status(self, ref, *, run_id):
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref, *, run_id):
        return "log"


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-gate-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
    )
    app: FastAPI = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            app.state.pipeline_adapter_provider = lambda _s: _FakeAdapter()
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                await AuthService(session, settings).create_user(
                    "operator", "op-pw", roles=["operator"]
                )
            yield client, settings, app


async def _seed_service(app) -> str:
    db: Database = app.state.db
    async with db.session() as session:
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.DEV,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        return svc.id


async def _seed_scan(app, git_sha, critical):
    db: Database = app.state.db
    async with db.session() as session:
        await ScanResultRepository(session).upsert(
            service="billing",
            git_sha=git_sha,
            scanner=Scanner.SONARQUBE,
            critical=critical,
            passed=(critical == 0),
        )


async def _token(client):
    resp = await client.post("/api/auth/login", json={"username": "operator", "password": "op-pw"})
    return resp.json()["data"]["access_token"]


async def test_deploy_blocked_when_critical(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    await _seed_scan(app, "sha-bad", critical=3)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {token}"},
        json={"version": "v1", "git_sha": "sha-bad"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "quality_gate_blocked"


async def test_deploy_passes_when_no_critical(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    await _seed_scan(app, "sha-ok", critical=0)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {token}"},
        json={"version": "v1", "git_sha": "sha-ok"},
    )
    assert resp.status_code == 202


async def test_deploy_passes_without_git_sha(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {token}"},
        json={"version": "v1"},
    )
    assert resp.status_code == 202


async def test_blocked_deploy_writes_audit(app_client):
    """门禁拦截也要留痕(§7.2):被 critical 挡下时落一条 service.deploy.blocked
    审计(FAILED),记录 git_sha 与被挡的 critical 数——即使 deploy 请求会话因 422
    回滚,审计仍独立提交,不随之丢失。"""
    from app.models.audit import AuditResult
    from app.services.audit_service import AuditService

    client, _, app = app_client
    service_id = await _seed_service(app)
    await _seed_scan(app, "sha-bad", critical=2)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers={"Authorization": f"Bearer {token}"},
        json={"version": "v1", "git_sha": "sha-bad"},
    )
    assert resp.status_code == 422

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AuditService(session).search(action="service.deploy.blocked")
    assert len(rows) == 1
    row = rows[0]
    assert row.result == AuditResult.FAILED
    assert row.target == f"service:{service_id}"
    assert row.after["git_sha"] == "sha-bad"
    assert row.after["blocking"]["critical"] == 2
