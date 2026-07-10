"""T0.12 网关中间件集成测试:安全响应头 + 全局限流 429。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client_with_tight_limit():
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_capacity=2,
        rate_limit_refill_per_sec=0,  # 不补充,便于测溢出
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_security_headers_present():
    app = create_app(Settings(rate_limit_enabled=False))
    with TestClient(app) as c:
        resp = c.get("/healthz")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "Referrer-Policy" in resp.headers
        assert "Content-Security-Policy" in resp.headers


def test_rate_limit_returns_429_after_capacity(client_with_tight_limit):
    c = client_with_tight_limit
    assert c.get("/healthz").status_code == 200
    assert c.get("/healthz").status_code == 200
    resp = c.get("/healthz")
    assert resp.status_code == 429
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "rate_limited"
    assert "Retry-After" in resp.headers


def test_rate_limit_disabled_lets_all_through():
    app = create_app(Settings(rate_limit_enabled=False))
    with TestClient(app) as c:
        for _ in range(20):
            assert c.get("/healthz").status_code == 200
