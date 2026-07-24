"""告警查询 API 验收(设计 §6.3 / §15.4)。

GET /api/alerts(前端 AlertsPage 依赖):
- 列出告警,最新在前。
- 按 status 过滤(只看 firing)。
- 按 service 过滤。
- 未认证 401。

告警入库由 Alertmanager webhook 完成;这里直接用仓储播种数据,只验查询端点。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.alert import AlertSeverity, AlertStatus
from app.models.base import Base
from app.services.alert_repository import AlertRepository
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def app_client(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-alerts-api-at-least-32-bytes",
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
            async with db.session() as session:
                repo = AlertRepository(session)
                await repo.upsert_from_alert(
                    fingerprint="fp-firing",
                    service="billing",
                    severity=AlertSeverity.CRITICAL,
                    summary="CPU 飙高",
                    status=AlertStatus.FIRING,
                )
                await repo.upsert_from_alert(
                    fingerprint="fp-resolved",
                    service="orders",
                    severity=AlertSeverity.WARNING,
                    summary="内存回落",
                    status=AlertStatus.RESOLVED,
                )
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_alerts_returns_all(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.get("/api/alerts", headers=_auth(token))
    assert resp.status_code == 200
    fingerprints = {a["fingerprint"] for a in resp.json()["data"]}
    assert fingerprints == {"fp-firing", "fp-resolved"}


async def test_list_alerts_filter_by_status(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.get("/api/alerts", headers=_auth(token), params={"status": "firing"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["fingerprint"] == "fp-firing"
    assert data[0]["severity"] == "critical"


async def test_list_alerts_filter_by_service(app_client):
    client, _, _ = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.get("/api/alerts", headers=_auth(token), params={"service": "orders"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["service"] == "orders"


async def test_list_alerts_requires_auth(app_client):
    client, _, _ = app_client
    resp = await client.get("/api/alerts")
    assert resp.status_code == 401
