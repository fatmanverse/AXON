"""T1.1 servers: schema 与 repository 的 CRUD 契约。"""

import pytest

from app.core.db import Database
from app.core.errors import AppError
from app.models.base import Base
from app.models.server import AccessMode, AgentStatus
from app.schemas.server import ServerCreate
from app.services.server_repository import ServerRepository


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_create_get_update_and_delete_ssh_server(db):
    payload = ServerCreate(
        name="orders-dev-01",
        host="10.0.0.10",
        access_mode=AccessMode.SSH,
        ssh_credential_id="cred_ssh_orders_dev",
        labels={"env": "dev", "region": "cn-shanghai"},
    )

    async with db.session() as session:
        repo = ServerRepository(session)
        created = await repo.create(payload)
        server_id = created.id
        assert created.agent_status == AgentStatus.UNKNOWN
        assert created.ssh_credential_id == "cred_ssh_orders_dev"

    async with db.session() as session:
        repo = ServerRepository(session)
        found = await repo.get(server_id)
        updated = await repo.update_labels(found.id, {"env": "staging"})
        assert updated.labels == {"env": "staging"}

    async with db.session() as session:
        repo = ServerRepository(session)
        await repo.delete(server_id)

    async with db.session() as session:
        with pytest.raises(AppError, match="服务器不存在"):
            await ServerRepository(session).get(server_id)


async def test_create_agent_server_requires_agent_id_and_keeps_agent_metadata(db):
    with pytest.raises(ValueError, match="agent_id"):
        ServerCreate(name="agent-node", host="10.0.0.11", access_mode=AccessMode.AGENT)

    payload = ServerCreate(
        name="agent-node",
        host="10.0.0.11",
        access_mode=AccessMode.AGENT,
        agent_id="agent-8f7c",
        agent_status=AgentStatus.ONLINE,
        agent_version="1.3.0",
    )
    async with db.session() as session:
        created = await ServerRepository(session).create(payload)
        assert created.ssh_credential_id is None
        assert created.agent_id == "agent-8f7c"
        assert created.agent_status == AgentStatus.ONLINE
