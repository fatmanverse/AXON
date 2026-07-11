"""T3.5 告警回调 webhook API 验收(§6.3/§8.3)。

覆盖:
- 合法签名 + Alertmanager 批量 payload → 按 fingerprint 幂等 upsert,返回 200。
- 签名无效 → 401。
- firing 后 resolved 幂等更新同一条并回填 resolved_at。
- 时间窗过期 → 401。
- 未配置 secret 的源 → 401。

复用 T2.4 的 HMAC 验签基建,构造真实签名。
"""

import hashlib
import hmac
import json
import time

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base

_SECRET = "wh-secret-alert"
_SOURCE = "alertmanager-main"


def _sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-alert-webhook",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        webhook_secrets={_SOURCE: _SECRET},
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            yield client, settings, app


def _headers(source: str, secret: str, body: bytes, *, ts: int | None = None) -> dict:
    ts = ts if ts is not None else int(time.time())
    return {
        "X-Webhook-Source": source,
        "X-Timestamp": str(ts),
        "X-Signature": _sign(secret, ts, body),
        "Content-Type": "application/json",
    }


def _payload(status: str = "firing", **overrides) -> dict:
    alert = {
        "fingerprint": "fp-1",
        "status": status,
        "labels": {"severity": "critical", "service": "billing"},
        "annotations": {"summary": "CPU 飙高"},
        "startsAt": "2026-07-11T12:00:00+00:00",
        "endsAt": "2026-07-11T13:00:00+00:00",
    }
    alert.update(overrides)
    return {"alerts": [alert]}


async def _post(client, payload, *, source=_SOURCE, secret=_SECRET, ts=None):
    body = json.dumps(payload).encode()
    return await client.post(
        "/api/webhooks/alert", content=body, headers=_headers(source, secret, body, ts=ts)
    )


async def test_valid_alert_webhook_upserts(app_client):
    client, _, app = app_client
    resp = await _post(client, _payload())
    assert resp.status_code == 200
    assert resp.json()["data"]["processed"] == 1

    from app.models.alert import AlertStatus
    from app.services.alert_repository import AlertRepository

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AlertRepository(session).list_alerts()
    assert len(rows) == 1
    assert rows[0].fingerprint == "fp-1"
    assert rows[0].service == "billing"
    assert rows[0].status == AlertStatus.FIRING


async def test_invalid_signature_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload()).encode()
    ts = int(time.time())
    bad = {
        "X-Webhook-Source": _SOURCE,
        "X-Timestamp": str(ts),
        "X-Signature": "deadbeef",
        "Content-Type": "application/json",
    }
    resp = await client.post("/api/webhooks/alert", content=body, headers=bad)
    assert resp.status_code == 401


async def test_firing_then_resolved_updates_same_row(app_client):
    client, _, app = app_client
    await _post(client, _payload(status="firing"))
    await _post(client, _payload(status="resolved"))

    from app.models.alert import AlertStatus
    from app.services.alert_repository import AlertRepository

    db: Database = app.state.db
    async with db.session() as session:
        rows = await AlertRepository(session).list_alerts()
    assert len(rows) == 1
    assert rows[0].status == AlertStatus.RESOLVED
    assert rows[0].resolved_at is not None


async def test_replay_outside_window_rejected(app_client):
    client, _, _ = app_client
    old = int(time.time()) - 600
    resp = await _post(client, _payload(), ts=old)
    assert resp.status_code == 401


async def test_unknown_source_rejected(app_client):
    client, _, _ = app_client
    resp = await _post(client, _payload(), source="ghost-src")
    assert resp.status_code == 401
