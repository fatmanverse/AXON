"""T4.3 生产接线验收:agent 模式服务器动作经真实 AgentGateway 执行(非 501 占位)。

审计发现的根因:executor_factory 恒返回无参 AgentGateway()(501),manager 从不
注入真实业务路径——真实应用里 access_mode=agent 的服务器动作永远命中占位。本测试
覆盖生产接线:注入 AgentGatewayRegistry 后,LifecycleService 对 agent 服务器的
动作真的经连接管理器下发 ServerCommand 并落 success。

同时锁死注册表的复用契约:多次取同一 agent 的 gateway 是同一实例(避免每次动作
重复注册 manager 回调导致内存泄漏)。
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from app.adapters.agent_gateway import AgentGateway
from app.adapters.agent_gateway_registry import AgentGatewayRegistry
from app.core.config import Settings
from app.core.db import Database
from app.core.secrets import build_secret_store
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import Runtime, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    ServerCommand,
)
from app.services.lifecycle_service import LifecycleService
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[ServerCommand] = []

    async def send(self, command: ServerCommand) -> None:
        self.sent.append(command)


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
def secrets():
    return build_secret_store(Settings(secret_backend="local", secret_master_key=""))


async def _seed_agent_service(db, agent_id: str) -> str:
    """建一个 agent 模式 server + 挂它的 systemd 服务放置,返回 service_id。"""
    async with db.session() as session:
        server = await ServerRepository(session).create(
            ServerCreate(
                name="edge-1",
                host="10.0.0.9",
                access_mode=AccessMode.AGENT,
                agent_id=agent_id,
            )
        )
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="collector",
                env=ServiceEnvironment.STAGING,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "collector.service"},
            )
        )
        await svc_repo.create_placement(PlacementCreate(service_id=service.id, server_id=server.id))
        return service.id


def test_registry_reuses_gateway_per_agent():
    """同一 agent_id 多次取回同一 gateway 实例(复用契约,防回调泄漏)。"""
    registry = AgentGatewayRegistry(AgentConnectionManager())
    gw1 = registry.for_agent("a1")
    gw2 = registry.for_agent("a1")
    gw3 = registry.for_agent("a2")
    assert gw1 is gw2
    assert gw1 is not gw3
    assert isinstance(gw1, AgentGateway)


async def test_agent_lifecycle_dispatches_via_real_gateway(db, secrets):
    """注入注册表后,agent 服务器的 restart 经连接管理器下发命令并落 success。

    之前恒 501 占位;此断言证明真实命令下发路径已接通(§5.3)。"""
    agent_id = "agent-collector"
    service_id = await _seed_agent_service(db, agent_id)

    manager = AgentConnectionManager()
    transport = _FakeTransport()
    manager.register(agent_id, transport, now=0.0)
    registry = AgentGatewayRegistry(manager, ack_timeout=1.0)

    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets, agent_registry=registry)

    async def _ack_when_sent():
        while not transport.sent:
            await asyncio.sleep(0.001)
        await manager.handle_inbound(
            AgentMessage(
                agent_id=agent_id,
                kind=AgentMessageKind.RESULT,
                task_id=transport.sent[-1].task_id,
                ok=True,
                detail="restarted",
            )
        )

    await asyncio.gather(
        svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART),
        _ack_when_sent(),
    )

    # 命令确实经连接管理器下发(agent 模式真实路径,非 501 占位)
    assert transport.sent
    assert transport.sent[-1].action == "exec"
    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.SUCCESS


async def test_agent_lifecycle_without_registry_marks_failed(db, secrets):
    """未注入注册表(纯 SSH/未开 gRPC)时,agent 服务器动作退回 501 占位 → task failed。

    这是诚实降级:不静默假装成功,与审计前的占位行为一致。"""
    service_id = await _seed_agent_service(db, "agent-x")
    async with db.session() as session:
        task = await TaskRepository(session).create(
            type=TaskType.RESTART, target=f"service:{service_id}", payload={}
        )
        task_id = task.id

    svc = LifecycleService(db, secrets)  # 未注入 agent_registry
    await svc.run_action(task_id=task_id, service_id=service_id, action=TaskType.RESTART)

    async with db.session() as session:
        refreshed = await TaskRepository(session).get(task_id)
        assert refreshed.status == TaskStatus.FAILED
        assert refreshed.error
