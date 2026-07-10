"""T0.3 TaskRepository:落库 + 状态流转守卫,用 aiosqlite。"""

import pytest

from app.core.db import Database
from app.models.base import Base
from app.models.task import TaskStatus, TaskType
from app.services.task_repository import TaskRepository


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_create_task_defaults_pending(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.create(
            type=TaskType.RESTART, target="svc:order", payload={"env": "dev"}, created_by="alice"
        )
        assert task.id
        assert task.status == TaskStatus.PENDING
        assert task.payload == {"env": "dev"}


async def test_mark_running_then_success(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.create(type=TaskType.DEPLOY, target="svc:web", created_by="bob")
        tid = task.id

    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.mark_running(tid)
    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.mark_result(tid, TaskStatus.SUCCESS, result={"ok": True})

    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.get(tid)
        assert task.status == TaskStatus.SUCCESS
        assert task.result == {"ok": True}
        assert task.finished_at is not None


async def test_illegal_transition_rejected(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.create(type=TaskType.STOP, target="svc:x", created_by="c")
        tid = task.id

    # pending -> success 非法(必须先 running)
    with pytest.raises(ValueError, match="非法状态流转"):
        async with db.session() as session:
            repo = TaskRepository(session)
            await repo.mark_result(tid, TaskStatus.SUCCESS)


async def test_mark_unknown_on_timeout(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.create(type=TaskType.RESTART, target="svc:y", created_by="d")
        tid = task.id
    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.mark_running(tid)
    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.mark_result(tid, TaskStatus.UNKNOWN, error="timeout")

    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.get(tid)
        assert task.status == TaskStatus.UNKNOWN
        assert task.error == "timeout"
