"""T0.10 WebSocket 端点集成测试:JWT 鉴权 + 订阅/心跳协议。

端到端"服务端推送到达客户端"由 test_ws_hub 的 Hub 单测覆盖(纯异步、可靠);
本文件覆盖端点侧能可靠验证的部分:鉴权拒绝、订阅应答、ping/pong。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.security import create_access_token
from app.main import create_app


@pytest.fixture
def app_and_settings():
    settings = Settings(rate_limit_enabled=False, jwt_secret="ws-test-secret-key-32bytes-long!!")
    return create_app(settings), settings


def _token(settings: Settings, sub: str = "u1") -> str:
    return create_access_token(
        subject=sub,
        secret=settings.jwt_secret,
        roles=["viewer"],
        algorithm=settings.jwt_algorithm,
        expires_minutes=5,
    )


def test_ws_rejects_missing_token(app_and_settings):
    app, _ = app_and_settings
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_ws_rejects_bad_token(app_and_settings):
    app, _ = app_and_settings
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=garbage"):
            pass


def test_ws_subscribe_acks(app_and_settings):
    app, settings = app_and_settings
    client = TestClient(app)
    with client.websocket_connect(f"/ws?token={_token(settings)}") as ws:
        ws.send_json({"action": "subscribe", "topic": "task:t1"})
        ack = ws.receive_json()
        assert ack["type"] == "subscribed"
        assert ack["topic"] == "task:t1"


def test_ws_ping_pong(app_and_settings):
    app, settings = app_and_settings
    client = TestClient(app)
    with client.websocket_connect(f"/ws?token={_token(settings)}") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_unsubscribe_acks(app_and_settings):
    app, settings = app_and_settings
    client = TestClient(app)
    with client.websocket_connect(f"/ws?token={_token(settings)}") as ws:
        ws.send_json({"action": "subscribe", "topic": "alerts"})
        ws.receive_json()
        ws.send_json({"action": "unsubscribe", "topic": "alerts"})
        ack = ws.receive_json()
        assert ack["type"] == "unsubscribed"
        assert ack["topic"] == "alerts"
