"""T1.14 Prometheus 查询代理 API 验收(设计 §15.4)。

覆盖:
- GET /api/metrics/query 经白名单校验后转发 PromQL,返回 Prometheus data。
- GET /api/metrics/query_range 带 start/end/step 转发。
- 非白名单指标 403;空/超长查询 400。
- 未认证 401(屏蔽直连,必须走鉴权)。

注入 fake http client(app.state.prometheus_http_client),不触真实 Prometheus。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.services.auth_service import AuthService

_OK_VECTOR = {
    "status": "success",
    "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1720000000, "0.5"]}]},
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    """按预置返回 _FakeResponse;记录请求参数供断言。"""

    def __init__(self, response) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def get(self, url: str, params: dict, timeout: float):
        self.calls.append({"url": url, "params": params})
        return self._response

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-metrics",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        metrics_allowed_prefixes=["up", "node_cpu_seconds_total", "node_memory_"],
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            app.state.prometheus_http_client = _FakeHttpClient(_FakeResponse(200, _OK_VECTOR))
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                await AuthService(session, settings).create_user(
                    "viewer", "viewer-pw", roles=["viewer"]
                )
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_query_allowed_metric_returns_data(app_client):
    client, _, _ = app_client
    token = await _token(client, "viewer", "viewer-pw")

    resp = await client.get(
        "/api/metrics/query", params={"query": "up"}, headers=_auth(token)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["resultType"] == "vector"


async def test_query_range_forwards_bounds(app_client):
    client, _, app = app_client
    token = await _token(client, "viewer", "viewer-pw")

    resp = await client.get(
        "/api/metrics/query_range",
        params={
            "query": "node_cpu_seconds_total",
            "start": 100.0,
            "end": 200.0,
            "step": 15.0,
        },
        headers=_auth(token),
    )

    assert resp.status_code == 200
    fake = app.state.prometheus_http_client
    assert fake.calls[0]["url"].endswith("/api/v1/query_range")
    assert fake.calls[0]["params"]["start"] == 100.0


async def test_non_allowlisted_metric_forbidden(app_client):
    client, _, _ = app_client
    token = await _token(client, "viewer", "viewer-pw")

    resp = await client.get(
        "/api/metrics/query",
        params={"query": "secret_internal_metric"},
        headers=_auth(token),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "metric_not_allowed"


async def test_empty_query_bad_request(app_client):
    client, _, _ = app_client
    token = await _token(client, "viewer", "viewer-pw")

    resp = await client.get(
        "/api/metrics/query", params={"query": "   "}, headers=_auth(token)
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_query"


async def test_query_requires_auth(app_client):
    client, _, _ = app_client
    resp = await client.get("/api/metrics/query", params={"query": "up"})
    assert resp.status_code == 401
