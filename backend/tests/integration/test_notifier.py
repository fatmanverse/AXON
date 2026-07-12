"""通知触达适配层验收(T3.11,设计 §13 通知触达)。

通知适配层把「一条通知」发到外部 IM(钉钉/飞书/企微/Slack)。这些渠道都用
POST JSON webhook 形态,故用统一 WebhookNotifier 承载,消息体由各渠道的 format
方法适配。覆盖:
- WebhookNotifier.notify 向 webhook URL POST 消息,HTTP 2xx 视为成功。
- HTTP 非 2xx / 网络异常 → 返回 False,不抛(通知失败不应阻断主流程)。
- format_alert / format_deploy 生成可读的中文摘要。
- NoopNotifier(未配置渠道时)notify 恒 True 且不发请求(通知是旁路,不阻断)。

用 fake http client,不触真实网络。
"""

from __future__ import annotations

import pytest

from app.services.notifier import (
    NoopNotifier,
    NotificationMessage,
    WebhookNotifier,
    format_alert_message,
    format_deploy_message,
)


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHttp:
    """记录 POST 调用的假 http client;可配置返回码或抛错。"""

    def __init__(self, *, status_code: int = 200, raise_exc: bool = False) -> None:
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def __aenter__(self) -> "_FakeHttp":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: object):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self.raise_exc:
            raise RuntimeError("network down")
        return _FakeResp(self.status_code)


async def test_webhook_notifier_posts_message():
    http = _FakeHttp(status_code=200)
    notifier = WebhookNotifier(webhook_url="https://im.example/hook", http_client=http)

    ok = await notifier.notify(NotificationMessage(title="部署成功", body="billing v1 → prod"))

    assert ok is True
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == "https://im.example/hook"
    assert http.calls[0]["method"] == "POST"


async def test_webhook_notifier_non_2xx_returns_false():
    http = _FakeHttp(status_code=500)
    notifier = WebhookNotifier(webhook_url="https://im.example/hook", http_client=http)

    ok = await notifier.notify(NotificationMessage(title="t", body="b"))

    assert ok is False


async def test_webhook_notifier_network_error_returns_false():
    http = _FakeHttp(raise_exc=True)
    notifier = WebhookNotifier(webhook_url="https://im.example/hook", http_client=http)

    ok = await notifier.notify(NotificationMessage(title="t", body="b"))

    # 通知失败不抛,只返回 False——通知是旁路,不应阻断主流程
    assert ok is False


async def test_noop_notifier_is_silent_success():
    notifier = NoopNotifier()
    ok = await notifier.notify(NotificationMessage(title="t", body="b"))
    assert ok is True


def test_format_alert_message_is_readable():
    msg = format_alert_message(
        severity="critical", summary="CPU>90% 持续5分钟", service="billing", status="firing"
    )
    assert "billing" in msg.body
    assert "critical" in msg.body.lower() or "严重" in msg.body
    assert "CPU>90%" in msg.body


def test_format_deploy_message_is_readable():
    msg = format_deploy_message(
        service="billing", env="prod", version="v1.2.0", operator="alice", action="部署"
    )
    assert "billing" in msg.body
    assert "prod" in msg.body
    assert "v1.2.0" in msg.body
    assert "alice" in msg.body
