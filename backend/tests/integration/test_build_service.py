"""BuildService 编排验收(构建能力一期,方案 A 本地构建)。

覆盖生产编排链路(注入 fake executor,不触真实 git/docker/子进程):
- success 路径:mark_running → clone/测试/build → 落 artifact → 回填 build.artifact_id
  → build.success + task.success;工作区用完清理。
- 失败路径:构建步骤非 0 → build.failed(带 error)+ task.failed;工作区仍清理。
- 未配 build_config:落 build.failed + task.failed(不抛穿回 ASGI 栈)。

BuildService 持 db(非 session),后台另起会话——照 AgentDeliveryService.run_install。
executor 经 executor_factory 注入,便于隔离真实子进程。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.config import Settings
from app.core.db import Database
from app.core.secrets import build_secret_store
from app.models.base import Base
from app.models.build import BuildSource, BuildStatus
from app.models.service import Runtime
from app.models.task import TaskStatus, TaskType
from app.schemas.service import ServiceCreate
from app.services.build_repository import BuildRepository
from app.services.build_service import BuildService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

_SHA = "c" * 40

_BUILD_CONFIG = {
    "repo_url": "https://git.example.com/team/app.git",
    "git_ref": "main",
    "test_command": "make test",
    "build_command": "make build",
    "artifact_type": "generic",
    "output_path": "dist",
}


class _FakeExecutor(Executor):
    """记录命令;rev-parse 回 sha,wc -c 回大小,可配置在某步失败。"""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.ran: list[str] = []
        self._fail_on = fail_on

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        if self._fail_on and self._fail_on in command:
            return CommandResult(exit_code=1, stdout="", stderr="boom")
        if "rev-parse" in command:
            return CommandResult(exit_code=0, stdout=f"{_SHA}\n", stderr="")
        if "wc -c" in command:
            return CommandResult(exit_code=0, stdout="4096\n", stderr="")
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest_asyncio.fixture
def settings():
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_backend="local",
        secret_master_key="",
    )


async def _seed_service(db: Database, *, build_config: dict | None) -> str:
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="app",
                env="dev",
                runtime=Runtime.DOCKER,
                runtime_ref={"image": "app"},
                build_config=build_config,
            )
        )
        return service.id


async def _seed_build(db: Database, service_id: str) -> tuple[str, str]:
    async with db.session() as session:
        build = await BuildRepository(session).create(
            service_id=service_id, source=BuildSource.UI_TRIGGERED, git_ref="main"
        )
        task = await TaskRepository(session).create(
            type=TaskType.BUILD,
            target=f"service:{service_id}",
            payload={},
            created_by="operator",
        )
        return build.id, task.id


def _make_service(db, settings, executor) -> BuildService:
    return BuildService(
        db,
        build_secret_store(settings),
        settings,
        executor_factory=lambda workdir: executor,
    )


async def test_build_success_creates_artifact_and_marks_success(db, settings):
    service_id = await _seed_service(db, build_config=_BUILD_CONFIG)
    build_id, task_id = await _seed_build(db, service_id)
    executor = _FakeExecutor()

    await _make_service(db, settings, executor).run_build(
        task_id=task_id, build_id=build_id, service_id=service_id
    )

    async with db.session() as session:
        build = await BuildRepository(session).get(build_id)
        assert build.status == BuildStatus.SUCCESS
        assert build.artifact_id is not None
        assert build.git_sha == _SHA
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.SUCCESS


async def test_build_failure_marks_failed(db, settings):
    service_id = await _seed_service(db, build_config=_BUILD_CONFIG)
    build_id, task_id = await _seed_build(db, service_id)
    executor = _FakeExecutor(fail_on="make build")

    await _make_service(db, settings, executor).run_build(
        task_id=task_id, build_id=build_id, service_id=service_id
    )

    async with db.session() as session:
        build = await BuildRepository(session).get(build_id)
        assert build.status == BuildStatus.FAILED
        assert build.error
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.FAILED


async def test_build_without_config_fails_gracefully(db, settings):
    service_id = await _seed_service(db, build_config=None)
    build_id, task_id = await _seed_build(db, service_id)
    executor = _FakeExecutor()

    # 不抛:结果落在 build/task 状态上
    await _make_service(db, settings, executor).run_build(
        task_id=task_id, build_id=build_id, service_id=service_id
    )

    async with db.session() as session:
        build = await BuildRepository(session).get(build_id)
        assert build.status == BuildStatus.FAILED
        task = await TaskRepository(session).get(task_id)
        assert task.status == TaskStatus.FAILED
