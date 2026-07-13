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


def test_cors_headers_present_for_whitelisted_origin():
    """T0.12 CORS 白名单:白名单 Origin 的预检返回 Access-Control-Allow-Origin。"""
    origin = "http://localhost:5173"
    app = create_app(Settings(rate_limit_enabled=False, cors_origins=[origin]))
    with TestClient(app) as c:
        resp = c.options(
            "/api/servers",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("Access-Control-Allow-Origin") == origin


def test_cors_blocks_non_whitelisted_origin():
    """非白名单 Origin 不回 Allow-Origin(浏览器据此拦截跨域读取)。"""
    app = create_app(Settings(rate_limit_enabled=False, cors_origins=["http://localhost:5173"]))
    with TestClient(app) as c:
        resp = c.options(
            "/api/servers",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("Access-Control-Allow-Origin") != "http://evil.example"


def test_request_body_over_limit_returns_413():
    """T0.12 请求体大小限制:超过上限的 body 返回 413,不进业务。"""
    app = create_app(Settings(rate_limit_enabled=False, max_request_body_bytes=100))
    with TestClient(app) as c:
        resp = c.post("/api/auth/login", content=b"x" * 200)
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "request_too_large"


def test_webhook_path_exempt_from_rate_limit():
    """T0.12 webhook 限流豁免:webhook 路径反复请求不触发 429(验收:不被误限流)。

    限流桶容量设为 1、零补充:若未豁免,第二次起必 429。webhook 走自身 HMAC
    鉴权(此处无有效签名,预期 401),但关键是路径永不被限流(不出现 429)。
    """
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_capacity=1,
        rate_limit_refill_per_sec=0,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        for _ in range(10):
            resp = c.post("/api/webhooks/deployment", content=b"{}")
            assert resp.status_code != 429


def test_non_webhook_still_rate_limited_with_exemption():
    """豁免只作用于 webhook 前缀,普通路径仍受限流(避免豁免过宽)。"""
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_capacity=2,
        rate_limit_refill_per_sec=0,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/healthz").status_code == 200
        assert c.get("/healthz").status_code == 429
