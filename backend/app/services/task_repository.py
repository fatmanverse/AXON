"""tasks 表仓储:创建与受状态机守卫的流转。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import realtime
from app.core.errors import AppError
from app.models.task import Task, TaskStatus, TaskType, ensure_transition


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        type: TaskType,
        target: str,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> Task:
        task = Task(type=type, target=target, payload=payload, created_by=created_by)
        self._session.add(task)
        await self._session.flush()
        return task

    async def create_deployment_operation(
        self,
        *,
        type: TaskType,
        service_id: str,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> Task:
        """创建受同 service 互斥约束的 deploy/rollback task。"""
        if type not in {TaskType.DEPLOY, TaskType.ROLLBACK}:
            raise ValueError("deployment operation 仅支持 deploy/rollback task")

        target = f"service:{service_id}"
        task = Task(type=type, target=target, payload=payload, created_by=created_by)
        try:
            async with self._session.begin_nested():
                self._session.add(task)
                await self._session.flush()
        except IntegrityError:
            active = await self.active_deployment_operation(target)
            if active is None:
                raise
            raise AppError(
                "deployment_in_progress",
                "该服务已有部署或回滚任务正在执行",
                status_code=409,
                details={"active_task_id": active.id},
            ) from None
        return task

    async def active_deployment_operation(self, target: str) -> Task | None:
        stmt = (
            select(Task)
            .where(
                Task.target == target,
                Task.type.in_((TaskType.DEPLOY, TaskType.ROLLBACK)),
                Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, task_id: str) -> Task:
        task = await self._session.get(Task, task_id)
        if task is None:
            raise AppError("task_not_found", "任务不存在", status_code=404)
        return task

    async def list_by_status(self, status: TaskStatus) -> list[Task]:
        result = await self._session.execute(select(Task).where(Task.status == status))
        return list(result.scalars().all())

    async def recent_rollbacks_for_target(self, target: str, *, since: datetime) -> list[Task]:
        """列出某目标(service:<id>)在 since 之后创建的 ROLLBACK task(告警自动回滚防抖用)。

        按 target + type + created_at 过滤;fingerprint 级判定由调用方读 payload 完成
        (JSON 键跨 sqlite/postgres 查询不可移植,故只在 SQL 里收窄到时间窗+目标+类型,
        再在 Python 里比对 payload.fingerprint)。
        """
        stmt = select(Task).where(
            Task.target == target,
            Task.type == TaskType.ROLLBACK,
            Task.created_at >= since,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_running(self, task_id: str) -> Task:
        task = await self.get(task_id)
        ensure_transition(task.status, TaskStatus.RUNNING)
        task.status = TaskStatus.RUNNING
        await self._session.flush()
        realtime.enqueue_task(task)
        return task

    async def mark_result(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Task:
        task = await self.get(task_id)
        ensure_transition(task.status, status)
        task.status = status
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if status.is_terminal() or status == TaskStatus.UNKNOWN:
            task.finished_at = datetime.now(UTC)
        await self._session.flush()
        realtime.enqueue_task(task)
        return task
