"""alerts 数据访问层(T3.5,§14.8/§6.3)。

Alertmanager 告警经 webhook 回调后落库。按 fingerprint 幂等 upsert:首次插入,
重复上报更新同一行(含 firing→resolved 流转,回填 resolved_at)。列表支持按
status/service 过滤,最新在前(供主页告警区)。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertSeverity, AlertStatus


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_from_alert(
        self,
        *,
        fingerprint: str,
        severity: AlertSeverity,
        summary: str,
        status: AlertStatus,
        service: str | None = None,
        source: str = "alertmanager",
        fired_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> Alert:
        """按 fingerprint 幂等 upsert。首次插入;重复上报更新状态与摘要。"""
        existing = (
            await self._session.execute(
                select(Alert).where(Alert.fingerprint == fingerprint)
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.status = status
            existing.severity = severity
            existing.summary = summary
            if service is not None:
                existing.service = service
            if fired_at is not None:
                existing.fired_at = fired_at
            if resolved_at is not None:
                existing.resolved_at = resolved_at
            await self._session.flush()
            return existing

        alert = Alert(
            fingerprint=fingerprint,
            service=service,
            severity=severity,
            summary=summary,
            source=source,
            status=status,
            fired_at=fired_at,
            resolved_at=resolved_at,
        )
        self._session.add(alert)
        await self._session.flush()
        return alert

    async def list_alerts(
        self,
        *,
        status: AlertStatus | None = None,
        service: str | None = None,
        limit: int = 100,
    ) -> Sequence[Alert]:
        """列出告警(可按 status/service 过滤),最新在前(供主页告警区)。"""
        stmt = select(Alert)
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        if service is not None:
            stmt = stmt.where(Alert.service == service)
        stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)
        return (await self._session.execute(stmt)).scalars().all()
