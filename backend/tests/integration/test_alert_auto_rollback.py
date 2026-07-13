"""T3.8 告警触发自动回滚 端到端(§11.2)。

alert webhook 收到 critical+firing 告警,若 settings.auto_rollback_on_alert 开启
且能按 (service, env) 定位到服务,则后台触发一次回滚(生成 ROLLBACK task + 新
deployment)。开关关闭 / 非 critical / 无法定位服务 均不触发。用 fake pipeline
adapter,不触真实 CI。
"""

import hashlib
import hmac
import json
import time

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
from app.services.deployment_repository import DeploymentRepository
from app.services.service_repository import ServiceRepository

_SECRET = "wh-secret-am"
_SOURCE = "alertmanager-main"


def _sign(secret: str, ts: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref, *, params):
        return "rb-run"

    async def get_status(self, ref, *, run_id):
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref, *, run_id):
        return "log"


def _make_settings(*, auto_rollback: bool) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-auto-rb",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        webhook_secrets={_SOURCE: _SECRET},
        auto_rollback_on_alert=auto_rollback,
    )


async def _boot(app):
    db: Database = app.state.db
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 建服务 + 一次成功部署(回滚目标)
    async with db.session() as session:
        svc = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        sid = svc.id
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=sid,
            env="prod",
            source=DeploymentSource.UI_TRIGGERED,
            version="v1",
            artifact="registry/app:v1",
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)
    return sid


def _alert_body(status="firing", severity="critical", service="billing", env="prod"):
    return json.dumps(
        {
            "alerts": [
                {
                    "fingerprint": "fp-rb-1",
                    "status": status,
                    "labels": {"severity": severity, "service": service, "env": env},
                    "annotations": {"summary": "服务宕机"},
                }
            ]
        }
    ).encode()


def _headers(body: bytes):
    ts = int(time.time())
    return {
        "X-Webhook-Source": _SOURCE,
        "X-Timestamp": str(ts),
        "X-Signature": _sign(_SECRET, ts, body),
        "Content-Type": "application/json",
    }


async def _deployment_count(app, sid):
    db: Database = app.state.db
    async with db.session() as session:
        rows = await DeploymentRepository(session).list_for_service(sid, env="prod")
    return rows


@pytest_asyncio.fixture
async def make_client():
    clients = []

    async def _factory(*, auto_rollback: bool):
        settings = _make_settings(auto_rollback=auto_rollback)
        app: FastAPI = create_app(settings)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://t")
        await client.__aenter__()
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        app.state.pipeline_adapter_provider = lambda _s: _FakeAdapter()
        sid = await _boot(app)
        clients.append((client, ctx, app))
        return client, app, sid

    yield _factory

    for client, ctx, _app in clients:
        await ctx.__aexit__(None, None, None)
        await client.__aexit__(None, None, None)


async def test_critical_alert_triggers_rollback_when_enabled(make_client):
    client, app, sid = await make_client(auto_rollback=True)
    body = _alert_body()
    resp = await client.post("/api/webhooks/alert", content=body, headers=_headers(body))
    assert resp.status_code == 200

    # 回滚生成新 deployment:原来 1 条,回滚后应 >= 2 条
    rows = await _deployment_count(app, sid)
    assert len(rows) >= 2


async def test_no_rollback_when_flag_off(make_client):
    client, app, sid = await make_client(auto_rollback=False)
    body = _alert_body()
    resp = await client.post("/api/webhooks/alert", content=body, headers=_headers(body))
    assert resp.status_code == 200
    rows = await _deployment_count(app, sid)
    assert len(rows) == 1  # 未触发回滚


async def test_no_rollback_for_warning(make_client):
    client, app, sid = await make_client(auto_rollback=True)
    body = _alert_body(severity="warning")
    resp = await client.post("/api/webhooks/alert", content=body, headers=_headers(body))
    assert resp.status_code == 200
    rows = await _deployment_count(app, sid)
    assert len(rows) == 1


async def test_debounce_skips_repeated_firing_same_fingerprint(make_client):
    """同一 fingerprint 连续两次 firing(抖动),防抖只触发一次回滚(§6.3)。

    第一次 POST 建 ROLLBACK task;第二次 POST 在防抖窗内查到同 fingerprint 的
    task,跳过不再建。断言:两次上报后 ROLLBACK task 仅 1 条。"""
    from app.models.task import TaskStatus, TaskType
    from app.services.task_repository import TaskRepository

    client, app, sid = await make_client(auto_rollback=True)
    body = _alert_body()

    r1 = await client.post("/api/webhooks/alert", content=body, headers=_headers(body))
    assert r1.status_code == 200
    assert r1.json()["data"]["auto_rollbacks"] == 1

    # 同 fingerprint 再次 firing(抖动)
    r2 = await client.post("/api/webhooks/alert", content=body, headers=_headers(body))
    assert r2.status_code == 200
    # 防抖命中:本次不再触发回滚
    assert r2.json()["data"]["auto_rollbacks"] == 0

    # 全局只建了一条 ROLLBACK task
    db: Database = app.state.db
    async with db.session() as session:
        rollbacks = [
            t
            for t in await TaskRepository(session).list_by_status(TaskStatus.SUCCESS)
            if t.type == TaskType.ROLLBACK
        ]
    assert len(rollbacks) == 1
