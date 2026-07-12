"""生产审批流 API(T2.15,设计 §10.2/§13)。

prod 高危操作(当前:deploy)在开关开启时先落 pending 审批(见 services.py 的
deploy 端点)。本模块提供审批的查询与决策:

- GET  /api/approvals            列出待审批(可按 env 过滤)。
- POST /api/approvals/{id}/approve  批准 → 建 task 执行原动作,回填 task_id。
- POST /api/approvals/{id}/reject   拒绝 → 关闭审批,记录理由。

鉴权:决策需 approval:{env}:approve 权限(operator/admin 具备)。批准后走与直接
部署完全一致的编排路径(DeploymentService),保证「批准执行」与「直接执行」无差异。
所有决策写审计(§13),固化「谁在何时批准了哪次生产变更」。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_database,
    get_pipeline_adapter_provider,
    get_session,
)
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.responses import ok
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.audit import AuditResult
from app.models.deployment import DeploymentStrategy
from app.models.user import User
from app.schemas.approval import ApprovalDecision, ApprovalOut
from app.services.approval_repository import ApprovalRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.deployment_service import DeployRequest, DeploymentService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _require_approve_permission(user: User, env: str) -> None:
    """决策审批需 approval:{env}:approve 权限,无权抛 403。"""
    required = Permission(resource="approval", env=env, action="approve")
    if not AuthService.permission_set(user).allows(required):
        raise AppError("forbidden", f"缺少权限: {required}", status_code=403)


def _approval_out(approval) -> dict:
    return ApprovalOut.model_validate(approval).model_dump(mode="json")


@router.get("")
async def list_approvals(
    env: str | None = Query(default=None, description="按环境过滤"),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """列出待审批(pending),最新在前;供审批面板展示。"""
    rows = await ApprovalRepository(session).list_pending(env=env)
    return ok([_approval_out(r) for r in rows])


@router.post("/{approval_id}/approve", status_code=202)
async def approve(
    approval_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    provider=Depends(get_pipeline_adapter_provider),
    user: User = Depends(get_current_user),
) -> dict:
    """批准一条待审批的高危操作,建 task 执行原动作(当前支持 deploy)。

    批准者需 approval:{env}:approve 权限,且不能是发起人本人(§13 四眼原则)。
    """
    approval = await ApprovalRepository(session).get(approval_id)
    _require_approve_permission(user, approval.env)
    if approval.status != ApprovalStatus.PENDING:
        raise AppError("approval_not_pending", "该审批已决策,不能重复操作", status_code=409)
    if approval.requested_by == user.username:
        raise AppError("self_approval_forbidden", "不能批准自己发起的操作", status_code=403)
    if approval.action != ApprovalAction.DEPLOY:
        raise AppError(
            "approval_action_unsupported",
            f"暂不支持批准执行的动作: {approval.action.value}",
            status_code=501,
        )
    if provider is None:
        raise AppError("pipeline_not_configured", "未配置 CI 流水线,无法执行部署", status_code=501)

    service = await ServiceRepository(session).get_service(approval.service_id)
    payload = approval.payload or {}
    version = payload.get("version", "")
    strategy = DeploymentStrategy(payload.get("strategy", DeploymentStrategy.ROLLING.value))
    git_sha = payload.get("git_sha")

    task = await TaskRepository(session).create(
        type="deploy",
        target=f"service:{approval.service_id}",
        payload={"env": approval.env, "version": version, "approval_id": approval_id},
        created_by=user.username,
    )
    task_id = task.id
    await ApprovalRepository(session).approve(approval_id, decided_by=user.username, task_id=task_id)
    await AuditService(session).record(
        actor=user.username,
        action="service.deploy.approved",
        target=f"service:{approval.service_id}",
        env=approval.env,
        result=AuditResult.SUCCESS,
        before={"requested_by": approval.requested_by},
        after={"approval_id": approval_id, "task_id": task_id, "version": version},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    deployer = DeploymentService(db, adapter_provider=provider)
    background.add_task(
        deployer.run_deploy,
        task_id=task_id,
        service_id=approval.service_id,
        request=DeployRequest(version=version, strategy=strategy, git_sha=git_sha),
        operator=approval.requested_by or user.username,
    )
    return ok({"approval_id": approval_id, "task_id": task_id, "status": "approved"})


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """拒绝一条待审批操作,记录理由;不执行任何动作。"""
    approval = await ApprovalRepository(session).get(approval_id)
    _require_approve_permission(user, approval.env)
    if approval.status != ApprovalStatus.PENDING:
        raise AppError("approval_not_pending", "该审批已决策,不能重复操作", status_code=409)

    updated = await ApprovalRepository(session).reject(
        approval_id, decided_by=user.username, reason=body.reason
    )
    await AuditService(session).record(
        actor=user.username,
        action="service.deploy.rejected",
        target=f"service:{approval.service_id}",
        env=approval.env,
        result=AuditResult.SUCCESS,
        before={"requested_by": approval.requested_by},
        after={"approval_id": approval_id, "reason": body.reason},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(_approval_out(updated))
