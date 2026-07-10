"""tasks 异步任务模型与状态机(§14.6)。

状态机规则:
- pending → running → (success | failed | unknown)
- unknown 是"超时/Agent 断连,可能已执行"的待核对态(§5.4),可再落定为 success/failed
- success / failed 为终态,不可回退;pending 不能直接跳终态(必须先 running)
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

# JSONB 在 PG 上更优;sqlite 回退到通用 JSON,保证本地测试可跑
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class TaskType(StrEnum):
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    START = "start"
    STOP = "stop"
    DELETE = "delete"
    RESTART = "restart"
    UPDATE_CONFIG = "update_config"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"

    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL: frozenset[TaskStatus] = frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED})

_ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.UNKNOWN}
    ),
    TaskStatus.UNKNOWN: frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED}),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def can_transition(src: TaskStatus, dst: TaskStatus) -> bool:
    return dst in _ALLOWED[src]


def ensure_transition(src: TaskStatus, dst: TaskStatus) -> None:
    if not can_transition(src, dst):
        raise ValueError(f"非法状态流转: {src.value} → {dst.value}")


def _uuid() -> str:
    return uuid.uuid4().hex


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="task_type"), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        default=TaskStatus.PENDING,
        nullable=False,
        index=True,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
