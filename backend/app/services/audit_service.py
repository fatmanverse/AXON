"""审计服务(§14.7):仅追加写入 + 多维检索。

刻意不提供 update / delete —— 审计记录不可篡改。写操作(部署/启停/删除/
改配置)统一经此落一条记录,便于回放与合规追溯。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog, AuditResult


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        result: AuditResult,
        env: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        ua: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor=actor,
            action=action,
            target=target,
            env=env,
            result=result,
            before=before,
            after=after,
            ip=ip,
            ua=ua,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def search(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        target: str | None = None,
        env: str | None = None,
        result: AuditResult | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        stmt = select(AuditLog)
        if actor is not None:
            stmt = stmt.where(AuditLog.actor == actor)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if target is not None:
            stmt = stmt.where(AuditLog.target == target)
        if env is not None:
            stmt = stmt.where(AuditLog.env == env)
        if result is not None:
            stmt = stmt.where(AuditLog.result == result)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        rows = await self._session.execute(stmt)
        return list(rows.scalars().all())
