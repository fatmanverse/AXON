"""T1.2 services + service_placements:逻辑定义与放置分离。"""

import pytest

from app.core.db import Database
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_k8s_service_has_serverless_placement(db):
    service_payload = ServiceCreate(
        name="checkout",
        env=ServiceEnvironment.PROD,
        runtime=Runtime.K8S,
        runtime_ref={"cluster_id": "core", "namespace": "commerce", "workload": "checkout"},
        desired_version="v2.4.1",
    )
    async with db.session() as session:
        repo = ServiceRepository(session)
        service = await repo.create_service(service_payload)
        placement = await repo.create_placement(PlacementCreate(service_id=service.id))

    assert placement.server_id is None
    assert placement.service_id == service.id


async def test_non_k8s_service_can_have_multiple_server_placements(db):
    async with db.session() as session:
        server_repo = ServerRepository(session)
        first_server = await server_repo.create(
            ServerCreate(
                name="billing-01",
                host="10.0.1.11",
                access_mode=AccessMode.SSH,
                ssh_credential_id="cred_billing_01",
            )
        )
        second_server = await server_repo.create(
            ServerCreate(
                name="billing-02",
                host="10.0.1.12",
                access_mode=AccessMode.SSH,
                ssh_credential_id="cred_billing_02",
            )
        )
        service_repo = ServiceRepository(session)
        service = await service_repo.create_service(
            ServiceCreate(
                name="billing",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
            )
        )
        await service_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=first_server.id)
        )
        await service_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=second_server.id)
        )
        placements = await service_repo.list_placements(service.id)

    assert {placement.server_id for placement in placements} == {first_server.id, second_server.id}


async def test_non_k8s_placement_requires_server_id(db):
    async with db.session() as session:
        repo = ServiceRepository(session)
        service = await repo.create_service(
            ServiceCreate(
                name="reporting",
                env=ServiceEnvironment.DEV,
                runtime=Runtime.DOCKER,
                runtime_ref={"container_name": "reporting"},
            )
        )
        with pytest.raises(ValueError, match="server_id"):
            await repo.create_placement(PlacementCreate(service_id=service.id))
