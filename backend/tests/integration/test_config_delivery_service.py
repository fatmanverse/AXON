"""配置下发编排验收(T2.12,设计 §12.2 / §15.3)。

覆盖 ConfigDeliveryService.run_delivery 的核心行为:
- 逐 placement 经 Executor 写配置文件到 target_path,并按 reload_mode reload/restart。
- 每个目标落一条 config_deliveries 记录(success / failed)。
- 下发前把内容里的 ${secret:credential_id} 注入保险箱真实值。
- 任一目标失败不影响其它目标(逐目标独立),整体 task 汇总成功/部分失败。
- 无 target_path 的配置版本明确失败(不知道写到哪)。

用 fake executor(记录写入)与内存保险箱,不触真实 SSH。
"""

from __future__ import annotations

import pytest_asyncio

from app.adapters.executor import CommandResult
from app.core.db import Database
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.base import Base
from app.models.config_delivery import DeliveryStatus
from app.models.server import AccessMode, Server
from app.models.service import (
    ReloadMode,
    Runtime,
    Service,
    ServiceEnvironment,
    ServicePlacement,
)
from app.models.task import Task, TaskStatus, TaskType
from app.services.config_delivery_repository import ConfigDeliveryRepository
from app.services.config_delivery_service import ConfigDeliveryService
from app.services.service_config_repository import ServiceConfigRepository


class FakeExecutor:
    """记录 update_config 与 exec 调用的假执行器。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.configs: list[tuple[str, str]] = []
        self.commands: list[str] = []

    async def update_config(self, path: str, content: str) -> CommandResult:
        self.configs.append((path, content))
        if self.fail:
            return CommandResult(exit_code=1, stdout="", stderr="disk full")
        return CommandResult(exit_code=0, stdout="written", stderr="")

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.commands.append(command)
        return CommandResult(exit_code=0, stdout="ok", stderr="")


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _seed_service(
    db, *, reload_mode=ReloadMode.RESTART, placements=1, target_path="/etc/app/app.env"
):
    """建一个 systemd 服务 + N 个 placement(各挂一台 SSH server)+ 一个配置版本。"""
    async with db.session() as session:
        service = Service(
            name="billing",
            env=ServiceEnvironment.DEV,
            runtime=Runtime.SYSTEMD,
            runtime_ref={"unit_name": "billing.service"},
            reload_mode=reload_mode,
        )
        session.add(service)
        await session.flush()
        for i in range(placements):
            server = Server(
                name=f"host-{i}",
                host=f"10.0.0.{i}",
                access_mode=AccessMode.SSH,
                ssh_credential_id="cred-x",
            )
            session.add(server)
            await session.flush()
            session.add(ServicePlacement(service_id=service.id, server_id=server.id))
        await session.flush()
        config = await ServiceConfigRepository(session).create_version(
            service_id=service.id,
            content="A=1",
            target_path=target_path,
            created_by="op",
        )
        service_id = service.id
        config_id = config.id
    return service_id, config_id


async def _make_task(db, service_id):
    async with db.session() as session:
        task = (
            await __import__("app.services.task_repository", fromlist=["TaskRepository"])
            .TaskRepository(session)
            .create(
                type=TaskType.UPDATE_CONFIG,
                target=f"service:{service_id}",
            )
        )
        return task.id


def _service(db, secrets, executors):
    """构造被测服务,executor 由 target host 顺序发放。"""
    it = iter(executors)

    def _connector_builder(server):
        return next(it)

    return ConfigDeliveryService(db, secrets, executor_builder=_connector_builder)


async def test_delivery_writes_config_and_restarts(db):
    service_id, config_id = await _seed_service(db, reload_mode=ReloadMode.RESTART)
    task_id = await _make_task(db, service_id)
    secrets = LocalSecretStore(master_key=generate_master_key())
    executor = FakeExecutor()

    svc = _service(db, secrets, [executor])
    await svc.run_delivery(task_id=task_id, config_id=config_id, operator="op")

    # 写了配置到 target_path
    assert executor.configs == [("/etc/app/app.env", "A=1")]
    # restart 模式:发了 restart 命令
    assert any("restart" in c for c in executor.commands)

    async with db.session() as session:
        deliveries = await ConfigDeliveryRepository(session).list_for_config(config_id)
        assert len(deliveries) == 1
        assert deliveries[0].status == DeliveryStatus.SUCCESS
        task = await session.get(Task, task_id)
        assert task.status == TaskStatus.SUCCESS


async def test_delivery_reload_mode_uses_reload(db):
    service_id, config_id = await _seed_service(db, reload_mode=ReloadMode.RELOAD)
    task_id = await _make_task(db, service_id)
    secrets = LocalSecretStore(master_key=generate_master_key())
    executor = FakeExecutor()

    svc = _service(db, secrets, [executor])
    await svc.run_delivery(task_id=task_id, config_id=config_id, operator="op")

    assert any("reload" in c for c in executor.commands)
    assert not any("restart" in c for c in executor.commands)


async def test_delivery_injects_secret(db):
    async with db.session() as session:
        service = Service(
            name="svc",
            env=ServiceEnvironment.DEV,
            runtime=Runtime.SYSTEMD,
            runtime_ref={"unit_name": "svc.service"},
            reload_mode=ReloadMode.RESTART,
        )
        session.add(service)
        await session.flush()
        server = Server(
            name="h", host="10.0.0.9", access_mode=AccessMode.SSH, ssh_credential_id="c"
        )
        session.add(server)
        await session.flush()
        session.add(ServicePlacement(service_id=service.id, server_id=server.id))
        secrets = LocalSecretStore(master_key=generate_master_key())
        cred_id = secrets.put("db-pw", "s3cr3t")
        config = await ServiceConfigRepository(session).create_version(
            service_id=service.id,
            content=f"PW=${{secret:{cred_id}}}",
            target_path="/etc/svc.env",
        )
        service_id, config_id = service.id, config.id
    task_id = await _make_task(db, service_id)
    executor = FakeExecutor()

    svc = _service(db, secrets, [executor])
    await svc.run_delivery(task_id=task_id, config_id=config_id, operator="op")

    # 占位符被替换为真实值
    assert executor.configs[0][1] == "PW=s3cr3t"


async def test_partial_failure_records_per_target(db):
    service_id, config_id = await _seed_service(db, placements=2)
    task_id = await _make_task(db, service_id)
    secrets = LocalSecretStore(master_key=generate_master_key())
    ok_exec = FakeExecutor(fail=False)
    bad_exec = FakeExecutor(fail=True)

    svc = _service(db, secrets, [ok_exec, bad_exec])
    await svc.run_delivery(task_id=task_id, config_id=config_id, operator="op")

    async with db.session() as session:
        deliveries = await ConfigDeliveryRepository(session).list_for_config(config_id)
        statuses = sorted(d.status.value for d in deliveries)
        assert statuses == ["failed", "success"]
        # 部分失败:整体 task 落 failed(有目标未成功)
        task = await session.get(Task, task_id)
        assert task.status == TaskStatus.FAILED


async def test_missing_target_path_fails(db):
    service_id, config_id = await _seed_service(db, target_path=None)
    task_id = await _make_task(db, service_id)
    secrets = LocalSecretStore(master_key=generate_master_key())
    executor = FakeExecutor()

    svc = _service(db, secrets, [executor])
    await svc.run_delivery(task_id=task_id, config_id=config_id, operator="op")

    async with db.session() as session:
        task = await session.get(Task, task_id)
        assert task.status == TaskStatus.FAILED
        # 没写任何文件
    assert executor.configs == []
