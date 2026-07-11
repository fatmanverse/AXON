"""任务进度查询 API(T1.11,设计 §15.2)。

GET /api/tasks/{task_id}:返回任务状态/结果/错误,供前端轮询(推送为主、
轮询兜底,§2)。各状态(含 unknown)如实返回;任务不存在 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.core.responses import ok
from app.models.user import User
from app.schemas.task import TaskOut
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    task = await TaskRepository(session).get(task_id)
    return ok(TaskOut.model_validate(task).model_dump())
