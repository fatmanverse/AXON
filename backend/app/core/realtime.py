"""实时推送编排:把业务状态变更投递到 WebSocket Hub(T0.10)。

设计要点:
- **提交后才推**:未提交/回滚的状态若先推给前端,会造成"看到又消失"。故 repo 层
  不直接 publish,而是把待推消息暂存到当前会话的 outbox(contextvar);由
  `Database.session()` 在 commit 成功后统一 flush 到 Hub,回滚则整批丢弃。
- **推送绝不影响业务写**:flush 时吞掉 Hub 异常(慢订阅者/背压),只记日志。前端
  有轮询兜底,偶发丢帧可容忍(§2)。
- **主题命名集中一处**:调用方只声明"某实体变了",不关心 topic 串怎么拼。

主题:
- ``task:<id>``  —— 单个任务状态流转(前端订阅自己发起的 task)。
- ``deployments`` —— 全局部署 feed(主页 Dashboard,§9.2)。
- ``alerts``      —— 全局告警区(主页告警面板,§6.3)。
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.core.ws_hub import get_hub

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.deployment import Deployment
    from app.models.task import Task

log = get_logger("realtime")

DEPLOYMENTS_TOPIC = "deployments"
ALERTS_TOPIC = "alerts"

# 当前会话的待推消息 outbox。None 表示当前不在带 outbox 的会话上下文里
# (此时 enqueue 静默丢弃——调用方在会话外,前端轮询兜底)。
_outbox: contextvars.ContextVar[list[tuple[str, dict[str, Any]]] | None] = contextvars.ContextVar(
    "realtime_outbox", default=None
)


def task_topic(task_id: str) -> str:
    return f"task:{task_id}"


def open_outbox() -> contextvars.Token:
    """开启一个新的会话 outbox,返回用于复位的 token(供 Database.session 调用)。"""
    return _outbox.set([])


def reset_outbox(token: contextvars.Token) -> list[tuple[str, dict[str, Any]]]:
    """取出当前 outbox 内容并复位到上一层,返回待推消息(供提交后 flush)。"""
    pending = _outbox.get() or []
    _outbox.reset(token)
    return pending


def _enqueue(topic: str, message: dict[str, Any]) -> None:
    outbox = _outbox.get()
    if outbox is None:
        # 不在会话上下文中(如后台脚本直接调 repo):无处暂存,交给轮询兜底。
        return
    outbox.append((topic, message))


async def flush(pending: list[tuple[str, dict[str, Any]]]) -> None:
    """把提交后的待推消息投递到 Hub。单条失败不影响其余,整体失败不上抛。"""
    if not pending:
        return
    hub = get_hub()
    for topic, message in pending:
        try:
            await hub.publish(topic, message)
        except Exception:  # noqa: BLE001 —— 推送失败绝不能影响业务
            log.warning("realtime_publish_failed", topic=topic)


def enqueue_task(task: Task) -> None:
    """任务状态流转:推给订阅该 task 的连接(前端轮询/推送二选一,§2)。"""
    _enqueue(
        task_topic(task.id),
        {
            "kind": "task",
            "id": task.id,
            "type": task.type.value,
            "status": task.status.value,
            "target": task.target,
            "result": task.result,
            "error": task.error,
        },
    )


def enqueue_deployment(deployment: Deployment) -> None:
    """部署记录变更:推到全局部署 feed(主页 Dashboard,§9.2)。"""
    _enqueue(
        DEPLOYMENTS_TOPIC,
        {
            "kind": "deployment",
            "id": deployment.id,
            "service_id": deployment.service_id,
            "env": deployment.env,
            "status": deployment.status.value,
            "source": deployment.source.value,
            "version": deployment.version,
        },
    )


def enqueue_alert(alert: Alert) -> None:
    """告警变更:推到全局告警区(主页告警面板,§6.3)。"""
    _enqueue(
        ALERTS_TOPIC,
        {
            "kind": "alert",
            "id": alert.id,
            "fingerprint": alert.fingerprint,
            "status": alert.status.value,
            "severity": alert.severity.value,
            "summary": alert.summary,
            "service": alert.service,
        },
    )
