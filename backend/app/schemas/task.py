"""tasks 的输出 schema(§14.6 / §15.2)。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.task import TaskStatus, TaskType


class TaskOut(BaseModel):
    """任务进度视图(供 T1.11 轮询)。result/error 按状态择一有值。"""

    id: str
    type: TaskType
    status: TaskStatus
    target: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskAccepted(BaseModel):
    """写操作异步受理响应:仅回 task_id 与初始状态,前端据此轮询/订阅。"""

    task_id: str
    status: TaskStatus
