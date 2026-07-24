"""scan_results 数据访问层(T3.1,§14.4/§7)。

扫描结果按 (git_sha, scanner) 幂等 upsert:同一提交同一扫描器重复上报更新
同一行,不产生重复。按 git_sha 列出供门禁查询与链路追溯;has_critical 供
部署卡点判断(存在任一扫描器报 critical 即拦截,§7.2)。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan_result import Scanner, ScanResult


class ScanResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        service: str,
        git_sha: str,
        scanner: Scanner,
        critical: int = 0,
        high: int = 0,
        medium: int = 0,
        low: int = 0,
        passed: bool = False,
        report_url: str | None = None,
    ) -> ScanResult:
        """按 (git_sha, scanner) 幂等 upsert:首次插入,重复上报更新同一行。"""
        stmt = select(ScanResult).where(
            ScanResult.git_sha == git_sha,
            ScanResult.scanner == scanner,
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()

        if existing is not None:
            existing.service = service
            existing.critical = critical
            existing.high = high
            existing.medium = medium
            existing.low = low
            existing.passed = passed
            existing.report_url = report_url
            await self._session.flush()
            return existing

        result = ScanResult(
            service=service,
            git_sha=git_sha,
            scanner=scanner,
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            passed=passed,
            report_url=report_url,
        )
        self._session.add(result)
        await self._session.flush()
        return result

    async def list_for_git_sha(self, git_sha: str) -> Sequence[ScanResult]:
        """列出某提交的全部扫描结果(各 scanner 一条),供门禁与链路追溯。"""
        stmt = select(ScanResult).where(ScanResult.git_sha == git_sha).order_by(ScanResult.scanner)
        return (await self._session.execute(stmt)).scalars().all()

    async def has_critical(self, git_sha: str) -> bool:
        """该提交是否存在任一扫描器报出的 critical 漏洞(部署卡点用,§7.2)。"""
        rows = await self.list_for_git_sha(git_sha)
        return any(r.critical > 0 for r in rows)
