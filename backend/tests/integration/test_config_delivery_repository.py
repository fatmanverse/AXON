"""config_deliveries 仓储验收(§14.5)。

用内存 sqlite 验证:
- create_pending 为一次下发批次逐目标建 pending 记录。
- mark_result 落 success/failed + result/error。
- list_for_config 返回某配置版本的全部下发记录(供逐目标展示)。
"""

import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.config_delivery import DeliveryStatus
from app.models.service import Runtime, ServiceEnvironment
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.config_delivery_repository import ConfigDeliveryRepository
from app.services.service_config_repository import ServiceConfigRepository
from app.services.service_repository import ServiceRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _seed_service_with_config(session):
    """建一个 systemd 服务 + 一个 placement + 一个配置版本,返回 (config_id, placement_id)。"""
    svc_repo = ServiceRepository(session)
    service = await svc_repo.create_service(
        ServiceCreate(
            name="billing",
            env=ServiceEnvironment.DEV,
            runtime=Runtime.SYSTEMD,
            runtime_ref={"unit_name": "billing.service"},
        )
    )
    # placement 需要一个 server_id;这里用占位 32 位串(下发编排才真正用到 server)
    placement = await svc_repo.create_placement(
        PlacementCreate(service_id=service.id, server_id="s" * 32)
    )
    config = await ServiceConfigRepository(session).create_version(
        service_id=service.id, content="A=1", target_path="/etc/billing/app.env"
    )
    return config.id, placement.id


async def test_create_pending_and_list(db):
    async with db.session() as session:
        config_id, placement_id = await _seed_service_with_config(session)
        repo = ConfigDeliveryRepository(session)
        rows = await repo.create_pending(config_id=config_id, placement_ids=[placement_id])
        assert len(rows) == 1
        assert rows[0].status == DeliveryStatus.PENDING

    async with db.session() as session:
        repo = ConfigDeliveryRepository(session)
        listing = await repo.list_for_config(config_id)
        assert len(listing) == 1
        assert listing[0].placement_id == placement_id


async def test_mark_result_success(db):
    async with db.session() as session:
        config_id, placement_id = await _seed_service_with_config(session)
        repo = ConfigDeliveryRepository(session)
        rows = await repo.create_pending(config_id=config_id, placement_ids=[placement_id])
        delivery_id = rows[0].id

    async with db.session() as session:
        repo = ConfigDeliveryRepository(session)
        updated = await repo.mark_result(
            delivery_id, status=DeliveryStatus.SUCCESS, result="reloaded"
        )
        assert updated.status == DeliveryStatus.SUCCESS
        assert updated.result == "reloaded"


async def test_mark_result_failed(db):
    async with db.session() as session:
        config_id, placement_id = await _seed_service_with_config(session)
        repo = ConfigDeliveryRepository(session)
        rows = await repo.create_pending(config_id=config_id, placement_ids=[placement_id])
        delivery_id = rows[0].id

    async with db.session() as session:
        repo = ConfigDeliveryRepository(session)
        updated = await repo.mark_result(
            delivery_id, status=DeliveryStatus.FAILED, error="connection refused"
        )
        assert updated.status == DeliveryStatus.FAILED
        assert updated.error == "connection refused"
