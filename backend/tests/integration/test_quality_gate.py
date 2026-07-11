"""T3.3 部署质量门禁(§7.2)。

check_quality_gate 在部署前查 scan_results,按策略决定放行/拦截:
- 策略关闭(block_on_critical=False):恒放行。
- 无 git_sha:放行(无从关联扫描,不阻断——MVP 宽松)。
- 有 critical 且策略开启:抛 QualityGateBlocked(带拦截原因)。
- 无 critical:放行。
- 无扫描记录:放行(未接扫描的服务不被门禁误伤,MVP)。

用内存 sqlite + scan 仓储,不 mock。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.models.base import Base
from app.models.scan_result import Scanner
from app.services.quality_gate import QualityGateBlocked, check_quality_gate
from app.services.scan_result_repository import ScanResultRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def _seed(db, git_sha, scanner, critical, passed=True):
    async with db.session() as session:
        await ScanResultRepository(session).upsert(
            service="billing",
            git_sha=git_sha,
            scanner=scanner,
            critical=critical,
            passed=passed,
        )


async def test_gate_blocks_when_critical_present(db):
    await _seed(db, "abc", Scanner.SONARQUBE, critical=2)
    async with db.session() as session:
        repo = ScanResultRepository(session)
        with pytest.raises(QualityGateBlocked):
            await check_quality_gate(repo, git_sha="abc", block_on_critical=True)


async def test_gate_passes_when_no_critical(db):
    await _seed(db, "abc", Scanner.SONARQUBE, critical=0)
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await check_quality_gate(repo, git_sha="abc", block_on_critical=True)


async def test_gate_passes_when_policy_disabled(db):
    await _seed(db, "abc", Scanner.SONARQUBE, critical=9)
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await check_quality_gate(repo, git_sha="abc", block_on_critical=False)


async def test_gate_passes_when_no_git_sha(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await check_quality_gate(repo, git_sha=None, block_on_critical=True)


async def test_gate_passes_when_no_scan_records(db):
    async with db.session() as session:
        repo = ScanResultRepository(session)
        await check_quality_gate(repo, git_sha="never-scanned", block_on_critical=True)
