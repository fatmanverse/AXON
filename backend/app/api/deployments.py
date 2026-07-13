"""顶层部署查询 API(T2.17,设计 §9.2/§15.4)。

主页 Dashboard 的部署 feed 需要**跨服务**的最近部署,而 /api/services/{id}/deployments
仅限单服务。本模块提供 GET /api/deployments?env=&limit= 聚合视图。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.core.responses import ok
from app.models.service import ServiceEnvironment
from app.models.user import User
from app.schemas.deployment import DeploymentOut
from app.services.deployment_repository import DeploymentRepository

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


@router.get("")
async def list_recent_deployments(
    env: ServiceEnvironment | None = Query(default=None, description="按环境过滤"),
    limit: int = Query(default=20, ge=1, le=100, description="返回条数上限"),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """跨服务最近部署(最新在前),供主页 Dashboard 部署 feed(§9.2)。"""
    rows = await DeploymentRepository(session).list_recent(
        env=env.value if env else None, limit=limit
    )
    return ok([DeploymentOut.model_validate(r).model_dump(mode="json") for r in rows])
