"""T0.6 审计日志:写操作留不可篡改记录,可按资源/人/环境检索(§14.7)。"""

import pytest

from app.core.db import Database
from app.models.audit import AuditResult
from app.models.base import Base
from app.services.audit_service import AuditService


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_record_persists_all_fields(db):
    async with db.session() as session:
        audit = AuditService(session)
        entry = await audit.record(
            actor="alice",
            action="service.delete",
            target="service:order",
            env="prod",
            result=AuditResult.SUCCESS,
            before={"status": "running"},
            after=None,
            ip="10.0.0.1",
            ua="curl/8.0",
        )
        assert entry.id

    async with db.session() as session:
        audit = AuditService(session)
        rows = await audit.search(actor="alice")
        assert len(rows) == 1
        r = rows[0]
        assert r.action == "service.delete"
        assert r.env == "prod"
        assert r.result == AuditResult.SUCCESS
        assert r.before == {"status": "running"}
        assert r.ip == "10.0.0.1"


async def test_search_by_target_and_env(db):
    async with db.session() as session:
        audit = AuditService(session)
        await audit.record(
            actor="a", action="deploy", target="svc:web", env="prod", result=AuditResult.SUCCESS
        )
        await audit.record(
            actor="b", action="deploy", target="svc:web", env="dev", result=AuditResult.SUCCESS
        )
        await audit.record(
            actor="c", action="restart", target="svc:api", env="prod", result=AuditResult.FAILED
        )

    async with db.session() as session:
        audit = AuditService(session)
        assert len(await audit.search(target="svc:web")) == 2
        assert len(await audit.search(env="prod")) == 2
        assert len(await audit.search(target="svc:web", env="prod")) == 1


async def test_records_are_append_only(db):
    """审计不可篡改:service 不暴露 update/delete。"""
    audit_attrs = dir(AuditService)
    assert "update" not in audit_attrs
    assert "delete" not in audit_attrs
