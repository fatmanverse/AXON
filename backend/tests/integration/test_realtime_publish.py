"""T0.10 实时推送接线:业务状态变更经 commit 后投递到 Hub。

覆盖此前的关键缺口——`hub.publish()` 从未被调用。这里验证真实链路:
repo 层状态流转 → Database.session() 提交后 → Hub 订阅者收到消息。
并验证事务语义:回滚的会话绝不推送(未提交状态不外泄给前端)。
"""

import asyncio

import pytest

from app.core import realtime
from app.core.db import Database
from app.core.ws_hub import get_hub
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.models.task import TaskType
from app.services.deployment_repository import DeploymentRepository
from app.services.task_repository import TaskRepository


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _drain(queue: asyncio.Queue, *, timeout: float = 1.0) -> dict:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def test_task_transition_publishes_after_commit(db):
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target="svc:web", created_by="alice"
        )
        tid = task.id

    # 订阅该 task 主题(订阅须在触发流转前建立,否则错过消息)
    sub = get_hub().subscribe(realtime.task_topic(tid))
    try:
        async with db.session() as session:
            await TaskRepository(session).mark_running(tid)

        message = await _drain(sub)
        assert message["kind"] == "task"
        assert message["id"] == tid
        assert message["status"] == "running"
    finally:
        get_hub().unsubscribe(realtime.task_topic(tid), sub)


async def test_rollback_does_not_publish(db):
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target="svc:x", created_by="bob"
        )
        tid = task.id

    sub = get_hub().subscribe(realtime.task_topic(tid))
    try:
        # 会话内流转后主动抛错触发回滚:提交未发生,故不应推送
        with pytest.raises(RuntimeError):
            async with db.session() as session:
                await TaskRepository(session).mark_running(tid)
                raise RuntimeError("boom")

        with pytest.raises(asyncio.TimeoutError):
            await _drain(sub, timeout=0.2)
    finally:
        get_hub().unsubscribe(realtime.task_topic(tid), sub)


async def test_deployment_create_publishes_to_feed(db):
    sub = get_hub().subscribe(realtime.DEPLOYMENTS_TOPIC)
    try:
        async with db.session() as session:
            await DeploymentRepository(session).create(
                service_id="svc1",
                env="dev",
                source=DeploymentSource.UI_TRIGGERED,
                version="1.2.3",
            )

        message = await _drain(sub)
        assert message["kind"] == "deployment"
        assert message["service_id"] == "svc1"
        assert message["status"] == DeploymentStatus.RUNNING.value
        assert message["version"] == "1.2.3"
    finally:
        get_hub().unsubscribe(realtime.DEPLOYMENTS_TOPIC, sub)
