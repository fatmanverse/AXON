"""T0.3 TaskRepository:落库 + 状态流转守卫,用 aiosqlite。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db import Database
from app.core.errors import AppError
from app.models.base import Base
from app.models.task import Task, TaskStatus, TaskType
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


async def test_create_deployment_operation_rejects_active_task_for_same_service(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        active = await repo.create_deployment_operation(
            type=TaskType.DEPLOY,
            service_id="svc-web",
            payload={"version": "v2"},
            created_by="alice",
        )

    with pytest.raises(AppError) as exc_info:
        async with db.session() as session:
            await TaskRepository(session).create_deployment_operation(
                type=TaskType.ROLLBACK,
                service_id="svc-web",
                payload={"target_deployment_id": "dep-v1"},
                created_by="bob",
            )

    assert exc_info.value.code == "deployment_in_progress"
    assert exc_info.value.status_code == 409
    assert exc_info.value.details == {"active_task_id": active.id}


async def test_create_deployment_operation_allows_other_service_and_terminal_release(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        first = await repo.create_deployment_operation(
            type=TaskType.DEPLOY,
            service_id="svc-a",
            created_by="alice",
        )
        other = await repo.create_deployment_operation(
            type=TaskType.ROLLBACK,
            service_id="svc-b",
            created_by="bob",
        )

    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.mark_running(first.id)
        await repo.mark_result(first.id, TaskStatus.SUCCESS)
        replacement = await repo.create_deployment_operation(
            type=TaskType.ROLLBACK,
            service_id="svc-a",
            created_by="carol",
        )

    assert other.target == "service:svc-b"
    assert replacement.target == "service:svc-a"


async def test_unknown_deployment_task_does_not_hold_exclusive_slot(db):
    async with db.session() as session:
        repo = TaskRepository(session)
        first = await repo.create_deployment_operation(
            type=TaskType.DEPLOY,
            service_id="svc-unknown",
        )
        await repo.mark_running(first.id)
        await repo.mark_result(first.id, TaskStatus.UNKNOWN, error="operator check required")
        replacement = await repo.create_deployment_operation(
            type=TaskType.ROLLBACK,
            service_id="svc-unknown",
        )

    assert replacement.id != first.id


async def test_partial_unique_index_rejects_direct_active_duplicate(db):
    async with db.session() as session:
        session.add(Task(type=TaskType.DEPLOY, target="service:svc-direct"))

    with pytest.raises(IntegrityError):
        async with db.session() as session:
            session.add(Task(type=TaskType.ROLLBACK, target="service:svc-direct"))
            await session.flush()


async def test_partial_unique_index_does_not_limit_other_task_types(db):
    async with db.session() as session:
        session.add_all(
            [
                Task(type=TaskType.RESTART, target="service:svc-lifecycle"),
                Task(type=TaskType.RESTART, target="service:svc-lifecycle"),
            ]
        )
