"""T3.1 scan_results 仓储(§14.4/§7)。

用内存 sqlite 验证:
- upsert 首次插入;同 (git_sha, scanner) 重复上报幂等更新同一行(§8.3)。
- 不同 scanner 各留一条。
- 按 git_sha 列出全部扫描结果。
- has_blocking(存在 critical)辅助门禁判断。
- 查无返回空。
"""

import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.scan_result import Scanner
from app.services.scan_result_repository import ScanResultRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_upsert_inserts_first(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        r = await repo.upsert(
            service="billing", git_sha="abc", scanner=Scanner.SONARQUBE,
            critical=0, high=1, medium=2, low=3, passed=True,
            report_url="https://sonar/x",
        )
        assert r.id
        assert r.passed is True
        assert r.high == 1


async def test_upsert_duplicate_updates_same_row(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        first = await repo.upsert(
            service="billing", git_sha="abc", scanner=Scanner.SONARQUBE, critical=5, passed=False,
        )
        first_id = first.id
    async with db.session() as session:
        repo = ScanResultRepository(session)
        second = await repo.upsert(
            service="billing", git_sha="abc", scanner=Scanner.SONARQUBE, critical=0, passed=True,
        )
        assert second.id == first_id
        rows = await repo.list_for_git_sha("abc")
        assert len(rows) == 1
        assert rows[0].passed is True
        assert rows[0].critical == 0


async def test_different_scanners_kept_separately(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await repo.upsert(service="billing", git_sha="abc", scanner=Scanner.SONARQUBE, passed=True)
        await repo.upsert(service="billing", git_sha="abc", 
            scanner=Scanner.TRIVY, critical=1, passed=False)
        rows = await repo.list_for_git_sha("abc")
        assert len(rows) == 2


async def test_list_empty_when_none(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        rows = await repo.list_for_git_sha("ghost")
        assert rows == []


async def test_has_blocking_true_when_critical_present(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await repo.upsert(service="billing", git_sha="abc", 
            scanner=Scanner.SONARQUBE, critical=0, passed=True)
        await repo.upsert(service="billing", git_sha="abc", 
            scanner=Scanner.TRIVY, critical=3, passed=False)
        assert await repo.has_critical("abc") is True


async def test_has_blocking_false_when_no_critical(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await repo.upsert(service="billing", git_sha="abc", 
            scanner=Scanner.SONARQUBE, critical=0, passed=True)
        assert await repo.has_critical("abc") is False
