"""LOW-1 关键操作通知接入验收(T3.11,§13 通知触达)。

审计发现 format_deploy_message 定义了却零调用——prod 关键操作(部署/删除/回滚)
不发通知。本测试证明:配了 notify_webhook_url 时,prod 部署/删除会向 webhook
POST 一条通知;dev 环境不通知(高频常规操作不打扰值班)。

用 fake http client(monkeypatch notifier._build_client)捕获通知,不触真实网络。
BackgroundTasks 在 ASGITransport 下于响应后执行,故断言在请求返回后读取捕获列表。
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
from app.schemas.environment import EnvironmentCreate
from app.schemas.service import ServiceCreate
from app.services import notifier as notifier_module
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository
from app.services.service_repository import ServiceRepository

_WEBHOOK_URL = "https://im.example/hook"


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref, *, params):
        return "run-1"

    async def get_status(self, ref, *, run_id):
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref, *, run_id):
        return "log"


class _FakeResp:
    status_code = 200


class _CapturingHttp:
    """捕获所有 POST 的假 http client;模块级 _build_client 被 patch 成返回它。"""

    calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def request(self, method, url, **kwargs):
        _CapturingHttp.calls.append({"method": method, "url": url, "json": kwargs.get("json")})
        return _FakeResp()


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    _CapturingHttp.calls = []
    monkeypatch.setattr(notifier_module, "_build_client", lambda: _CapturingHttp())
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-notify-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        require_prod_approval=False,  # 直接执行路径(否则 prod 落审批不触发通知)
        notify_webhook_url=_WEBHOOK_URL,
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
                # 通知由环境的 requires_approval 语义驱动(§10.2):prod 声明需审批→关键
                # 操作通知;dev 不需审批→高频常规操作不打扰值班。故 seed 两环境。
                env_repo = EnvironmentRepository(session)
                await env_repo.create(EnvironmentCreate(name="prod", requires_approval=True))
                await env_repo.create(EnvironmentCreate(name="dev"))
            yield client, app


async def _seed_service(app, *, env: ServiceEnvironment) -> str:
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


async def _token(client) -> str:
    resp = await client.post("/api/auth/login", json={"username": "operator", "password": "op-pw"})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_prod_deploy_sends_notification(app_client):
    client, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1.2.0"},
    )
    assert resp.status_code == 202

    assert len(_CapturingHttp.calls) == 1
    call = _CapturingHttp.calls[0]
    assert call["url"] == _WEBHOOK_URL
    text = call["json"]["text"]
    assert "billing" in text
    assert "v1.2.0" in text
    assert "部署" in text


async def test_prod_delete_sends_notification(app_client):
    client, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.PROD)
    token = await _token(client)

    resp = await client.delete(f"/api/services/{service_id}", headers=_auth(token))
    assert resp.status_code == 202

    assert len(_CapturingHttp.calls) == 1
    assert "删除" in _CapturingHttp.calls[0]["json"]["text"]


async def test_dev_deploy_does_not_notify(app_client):
    client, app = app_client
    service_id = await _seed_service(app, env=ServiceEnvironment.DEV)
    token = await _token(client)

    resp = await client.post(
        f"/api/services/{service_id}/deploy",
        headers=_auth(token),
        json={"version": "v1"},
    )
    assert resp.status_code == 202
    # dev 环境不推送通知(仅 prod 关键操作通知)
    assert _CapturingHttp.calls == []
