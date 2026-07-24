"""T3.5 alerts 仓储(§14.8/§6.3)。

用内存 sqlite 验证:
- upsert_from_alert 首次插入(firing)。
- 同 fingerprint 重复上报幂等更新同一行。
- firing → resolved 状态流转回填 resolved_at。
- 按 status 列出(如只看 firing)。
- 按 service 列出。
"""

from datetime import UTC, datetime

import pytest_asyncio

from app.core.db import Database
from app.models.alert import AlertSeverity, AlertStatus
from app.models.base import Base
from app.services.alert_repository import AlertRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_first_alert_inserts_firing(db):
    async with db.session() as session:
        repo = AlertRepository(session)
        alert = await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.CRITICAL,
            summary="CPU 飙高",
            status=AlertStatus.FIRING,
            fired_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        )
        assert alert.id
        assert alert.status == AlertStatus.FIRING
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.resolved_at is None


async def test_duplicate_fingerprint_updates_same_row(db):
    async with db.session() as session:
        repo = AlertRepository(session)
        first = await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.WARNING,
            summary="内存高",
            status=AlertStatus.FIRING,
        )
        first_id = first.id
    async with db.session() as session:
        repo = AlertRepository(session)
        second = await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.WARNING,
            summary="内存高",
            status=AlertStatus.FIRING,
        )
        assert second.id == first_id
        rows = await repo.list_alerts()
        assert len(rows) == 1


async def test_resolve_fills_resolved_at(db):
    resolved_ts = datetime(2026, 7, 11, 13, 0, tzinfo=UTC)
    async with db.session() as session:
        repo = AlertRepository(session)
        await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.CRITICAL,
            summary="宕机",
            status=AlertStatus.FIRING,
        )
    async with db.session() as session:
        repo = AlertRepository(session)
        resolved = await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.CRITICAL,
            summary="宕机",
            status=AlertStatus.RESOLVED,
            resolved_at=resolved_ts,
        )
        assert resolved.status == AlertStatus.RESOLVED
        assert resolved.resolved_at is not None


async def test_list_by_status(db):
    async with db.session() as session:
        repo = AlertRepository(session)
        await repo.upsert_from_alert(
            fingerprint="fp1",
            service="a",
            severity=AlertSeverity.CRITICAL,
            summary="x",
            status=AlertStatus.FIRING,
        )
        await repo.upsert_from_alert(
            fingerprint="fp2",
            service="b",
            severity=AlertSeverity.INFO,
            summary="y",
            status=AlertStatus.RESOLVED,
        )
        firing = await repo.list_alerts(status=AlertStatus.FIRING)
        assert len(firing) == 1
        assert firing[0].fingerprint == "fp1"


async def test_list_by_service(db):
    async with db.session() as session:
        repo = AlertRepository(session)
        await repo.upsert_from_alert(
            fingerprint="fp1",
            service="billing",
            severity=AlertSeverity.WARNING,
            summary="x",
            status=AlertStatus.FIRING,
        )
        await repo.upsert_from_alert(
            fingerprint="fp2",
            service="orders",
            severity=AlertSeverity.WARNING,
            summary="y",
            status=AlertStatus.FIRING,
        )
        rows = await repo.list_alerts(service="billing")
        assert len(rows) == 1
        assert rows[0].service == "billing"
