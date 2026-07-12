"""告警查询 API(§6.3 / §15.4)。

Alertmanager 告警经 webhook(见 app/api/webhooks.py 的 /alert)幂等落库,本端点
只读:列出告警(可按 status/service 过滤),最新在前,供主页告警区与告警页展示。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.core.responses import ok
from app.models.alert import AlertStatus
from app.models.user import User
from app.schemas.alert import AlertOut
from app.services.alert_repository import AlertRepository

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    status: AlertStatus | None = Query(default=None, description="按状态过滤"),
    service: str | None = Query(default=None, description="按服务过滤"),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await AlertRepository(session).list_alerts(status=status, service=service, limit=limit)
    return ok([AlertOut.model_validate(r).model_dump(mode="json") for r in rows])
