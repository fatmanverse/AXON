"""T2.4 入向部署 webhook API 验收(设计 §8.2 / §8.3)。

覆盖:
- 有效 HMAC 签名 + 合法 payload → upsert deployment 并返回 200。
- 幂等:同 (pipeline_id, service, env) 重复上报只留一条,状态幂等更新。
- 签名无效 / 缺签名 → 401。
- 时间窗过期(重放)→ 401。
- 未知服务 → 404(明确拒绝,不静默建记录)。
- rolled_back 状态上报被拒(该态由控制面回滚闭环产生,不接受外部上报)。

用 HMAC 对 (timestamp + body) 签名,不依赖真实 CI。
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
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.service_repository import ServiceRepository

_SOURCE = "gitlab-main"
_SECRET = "webhook-secret-gitlab"


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-webhook-at-least-32-bytes",
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
            async with db.session() as session:
                await AuthService(session, settings).create_user(
                    "admin", "admin-pw", roles=["admin"]
                )
                await ServiceRepository(session).create_service(
                    ServiceCreate(
                        name="billing",
                        env=ServiceEnvironment.PROD,
                        runtime=Runtime.SYSTEMD,
                        runtime_ref={"unit_name": "billing.service"},
                    )
                )
            yield client, settings, app


def _sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


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
        "env": "prod",
        "pipeline_id": "pipe-100",
        "status": "success",
        "version": "v1.2.0",
        "git_sha": "abc123",
    }
    base.update(overrides)
    return base


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


async def test_valid_webhook_upserts_deployment(app_client):
    client, _, app = app_client
    body = json.dumps(_payload()).encode()

    resp = await client.post(
        "/api/webhooks/deployment",
        content=body,
        headers=_headers(_SOURCE, _SECRET, body),
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 查部署历史应有一条 success 记录
    token = await _token(client, "admin", "admin-pw")
    db: Database = app.state.db
    async with db.session() as session:
        svc = await ServiceRepository(session).get_by_name_env("billing", "prod")
    listed = await client.get(
        f"/api/services/{svc.id}/deployments",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["source"] == "pipeline-webhook"
    assert rows[0]["pipeline_id"] == "pipe-100"


async def test_duplicate_webhook_is_idempotent(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload(status="running")).encode()
    await client.post(
        "/api/webhooks/deployment", content=body, headers=_headers(_SOURCE, _SECRET, body)
    )
    # 同 pipeline_id 再报 success
    body2 = json.dumps(_payload(status="success")).encode()
    resp = await client.post(
        "/api/webhooks/deployment", content=body2, headers=_headers(_SOURCE, _SECRET, body2)
    )
    assert resp.status_code == 200


async def test_invalid_signature_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload()).encode()
    headers = _headers(_SOURCE, "wrong-secret", body)
    resp = await client.post("/api/webhooks/deployment", content=body, headers=headers)
    assert resp.status_code == 401


async def test_missing_signature_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload()).encode()
    resp = await client.post(
        "/api/webhooks/deployment",
        content=body,
        headers={"X-Webhook-Source": _SOURCE, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_replay_outside_window_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload()).encode()
    old = int(time.time()) - 600
    resp = await client.post(
        "/api/webhooks/deployment",
        content=body,
        headers=_headers(_SOURCE, _SECRET, body, ts=old),
    )
    assert resp.status_code == 401


async def test_unknown_source_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload()).encode()
    resp = await client.post(
        "/api/webhooks/deployment",
        content=body,
        headers=_headers("unknown-src", _SECRET, body),
    )
    assert resp.status_code == 401


async def test_unknown_service_returns_404(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload(service="ghost")).encode()
    resp = await client.post(
        "/api/webhooks/deployment", content=body, headers=_headers(_SOURCE, _SECRET, body)
    )
    assert resp.status_code == 404


async def test_rolled_back_status_rejected(app_client):
    client, _, _ = app_client
    body = json.dumps(_payload(status="rolled_back")).encode()
    resp = await client.post(
        "/api/webhooks/deployment", content=body, headers=_headers(_SOURCE, _SECRET, body)
    )
    assert resp.status_code == 400
