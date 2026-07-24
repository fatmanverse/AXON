"""T3.2 扫描回流 webhook API 验收(§7.1/§8.3)。

覆盖:合法签名 upsert、签名无效 401、幂等重复上报、时间窗过期、未知源。
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

_SECRET = "wh-secret-sonar"
_SOURCE = "sonarqube-main"


def _sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-scan-webhook-at-least-32-bytes",
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


def _payload(**overrides) -> dict:
    base = {
        "service": "billing",
        "git_sha": "abc123",
        "scanner": "sonarqube",
        "critical": 0,
        "high": 2,
        "passed": True,
        "report_url": "https://sonar/x",
    }
    base.update(overrides)
    return base


async def _post(client, payload, *, source=_SOURCE, secret=_SECRET, ts=None):
    body = json.dumps(payload).encode()
    return await client.post(
        "/api/webhooks/scan", content=body, headers=_headers(source, secret, body, ts=ts)
    )


async def test_valid_scan_webhook_upserts(app_client):
    client, _, app = app_client
    resp = await _post(client, _payload())
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from app.services.scan_result_repository import ScanResultRepository

    db: Database = app.state.db
    async with db.session() as session:
        rows = await ScanResultRepository(session).list_for_git_sha("abc123")
    assert len(rows) == 1
    assert rows[0].scanner.value == "sonarqube"
    assert rows[0].high == 2


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
    resp = await client.post("/api/webhooks/scan", content=body, headers=bad)
    assert resp.status_code == 401


async def test_idempotent_duplicate_updates_same_row(app_client):
    client, _, app = app_client
    await _post(client, _payload(critical=5, passed=False))
    await _post(client, _payload(critical=0, passed=True))

    from app.services.scan_result_repository import ScanResultRepository

    db: Database = app.state.db
    async with db.session() as session:
        rows = await ScanResultRepository(session).list_for_git_sha("abc123")
    assert len(rows) == 1
    assert rows[0].passed is True
    assert rows[0].critical == 0


async def test_replay_outside_window_rejected(app_client):
    client, _, _ = app_client
    old = int(time.time()) - 600
    resp = await _post(client, _payload(), ts=old)
    assert resp.status_code == 401


async def test_unknown_source_rejected(app_client):
    client, _, _ = app_client
    resp = await _post(client, _payload(), source="ghost-src")
    assert resp.status_code == 401
