"""环境管理 API(自定义环境管理)。

职责:
- POST   /api/environments        创建环境(写审计,environment:*:write 权限)。
- GET    /api/environments        列出环境(按 name 排序),供服务器/服务纳管选择。
- DELETE /api/environments/{id}   删除环境(写审计,environment:*:delete 权限)。

环境是 services/servers 的 env 段真相源,无预置数据。requires_approval 决定该
环境的高危操作是否走审批流(取代旧的 env == prod 硬编码判定)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, require_permission
from app.core.permissions import parse_permission
from app.core.responses import ok
from app.models.audit import AuditResult
from app.models.environment import Environment
from app.models.user import User
from app.schemas.environment import EnvironmentCreate, EnvironmentOut
from app.services.audit_service import AuditService
from app.services.environment_repository import EnvironmentRepository

router = APIRouter(prefix="/api/environments", tags=["environments"])


def _environment_out(env: Environment) -> dict:
    return EnvironmentOut.model_validate(env).model_dump()


@router.post("", status_code=201)
async def create_environment(
    body: EnvironmentCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("environment:*:write"))),
) -> dict:
    env = await EnvironmentRepository(session).create(body)
    await AuditService(session).record(
        actor=user.username,
        action="environment.create",
        target=f"environment:{env.id}",
        env=env.name,
        result=AuditResult.SUCCESS,
        after={"name": env.name, "requires_approval": env.requires_approval},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(_environment_out(env))


@router.get("")
async def list_environments(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await EnvironmentRepository(session).list()
    return ok([_environment_out(e) for e in rows])


@router.delete("/{environment_id}")
async def delete_environment(
    environment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("environment:*:delete"))),
) -> dict:
    repo = EnvironmentRepository(session)
    env = await repo.get(environment_id)
    before = {"name": env.name, "requires_approval": env.requires_approval}
    await repo.delete(environment_id)
    await AuditService(session).record(
        actor=user.username,
        action="environment.delete",
        target=f"environment:{environment_id}",
        env=env.name,
        result=AuditResult.SUCCESS,
        before=before,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})
