"""通知触达适配层(T3.11,设计 §13 通知触达)。

关键操作(prod 部署/删除)与告警触发时,把一条通知推送到外部 IM。钉钉/飞书/
企微/Slack 的自定义机器人都是「POST JSON 到一个 webhook URL」形态,故用统一的
WebhookNotifier 承载,payload 结构走通用 {title, text} —— 生产接具体渠道时按其
报文规范微调 _build_payload 即可,上层调用不变(§2 Adapter 屏蔽差异)。

设计要点:
- 通知是旁路:失败(非 2xx / 网络异常)只返回 False 并记日志,绝不抛出阻断主流程
  (部署/回滚/告警处理不能因为 IM 挂了而失败)。
- 未配置渠道时用 NoopNotifier:notify 恒成功且不发请求,让调用方无需到处判空。
- http client 依赖注入(生产传 httpx.AsyncClient,测试传 fake),与 pipeline adapter
  一致的可测形态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

log = get_logger("notifier")

DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class NotificationMessage:
    """一条通知:标题 + 正文。渠道适配器据此拼各自的报文。"""

    title: str
    body: str


@runtime_checkable
class Notifier(Protocol):
    """通知渠道统一接口。notify 返回是否送达,失败不抛(通知是旁路)。"""

    async def notify(self, message: NotificationMessage) -> bool: ...


class HttpClientLike(Protocol):
    """httpx.AsyncClient 的最小子集(request + async 上下文)。"""

    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...
    async def __aenter__(self) -> HttpClientLike: ...
    async def __aexit__(self, *exc: Any) -> None: ...


def _build_client() -> HttpClientLike:
    import httpx

    return httpx.AsyncClient()


class NoopNotifier:
    """未配置通知渠道时的空实现:恒成功、不发请求。"""

    async def notify(self, message: NotificationMessage) -> bool:
        return True


class WebhookNotifier:
    """通用 webhook 渠道:POST JSON 到配置的 URL(钉钉/飞书/企微/Slack 自定义机器人)。"""

    def __init__(
        self,
        *,
        webhook_url: str,
        http_client: HttpClientLike | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = webhook_url
        self._http = http_client
        self._timeout = timeout

    def _build_payload(self, message: NotificationMessage) -> dict[str, Any]:
        """通用文本报文。多数 IM 自定义机器人接受 {title, text} 近似结构;
        接具体渠道(如钉钉 markdown、飞书 post)时在此按其规范细化。"""
        return {"title": message.title, "text": f"{message.title}\n{message.body}"}

    async def notify(self, message: NotificationMessage) -> bool:
        """POST 通知到 webhook。2xx 视为送达;非 2xx / 异常返回 False,不抛。"""
        try:
            http = self._http if self._http is not None else _build_client()
            async with http as conn:
                resp = await conn.request(
                    "POST", self._url, json=self._build_payload(message), timeout=self._timeout
                )
        except Exception as exc:
            log.warning("notify_failed", error_type=type(exc).__name__)
            return False

        ok = 200 <= resp.status_code < 300
        if not ok:
            log.warning("notify_non_2xx", status_code=resp.status_code)
        return ok


def build_notifier(settings: Any) -> Notifier:
    """按配置构造通知渠道。配了 notify_webhook_url 用 WebhookNotifier,否则 Noop
    (未配置渠道即静默,不阻断主流程)。切换渠道不改调用方(§2 Adapter 屏蔽差异)。"""
    url = getattr(settings, "notify_webhook_url", "")
    if url:
        return WebhookNotifier(webhook_url=url)
    return NoopNotifier()


def format_alert_message(
    *, severity: str, summary: str, service: str | None, status: str
) -> NotificationMessage:
    """把一条告警格式化为可读中文通知(§6.3 告警联动)。"""
    svc = service or "(未关联服务)"
    title = f"[告警] {severity} · {svc}"
    body = f"服务: {svc}\n级别: {severity}\n状态: {status}\n摘要: {summary}"
    return NotificationMessage(title=title, body=body)


def format_deploy_message(
    *, service: str, env: str, version: str, operator: str, action: str
) -> NotificationMessage:
    """把一次关键操作(部署/回滚/删除)格式化为可读中文通知(§13)。"""
    title = f"[{action}] {service} · {env}"
    body = f"服务: {service}\n环境: {env}\n版本: {version}\n操作人: {operator}\n动作: {action}"
    return NotificationMessage(title=title, body=body)
