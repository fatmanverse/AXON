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
    get_agent_registry,
    get_current_user,
    get_database,
    get_health_checker,
    get_k8s_api_factory,
    get_pipeline_adapter_provider,
    get_rollout_provider,
    get_secret_store,
    get_session,
    get_ssh_connector,
)
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.responses import ok
from app.core.secrets import SecretStore
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.audit import AuditResult
from app.models.deployment import DeploymentStrategy
from app.models.task import TaskType
from app.models.user import User
from app.schemas.approval import ApprovalDecision, ApprovalOut
from app.services.approval_repository import ApprovalRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.deployment_service import DeploymentService, DeployRequest
from app.services.lifecycle_service import LifecycleService
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


_APPROVAL_TASK_TYPE: dict[ApprovalAction, TaskType] = {
    ApprovalAction.DEPLOY: TaskType.DEPLOY,
    ApprovalAction.ROLLBACK: TaskType.ROLLBACK,
    ApprovalAction.DELETE: TaskType.DELETE,
}


@router.post("/{approval_id}/approve", status_code=202)
async def approve(
    approval_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    provider=Depends(get_pipeline_adapter_provider),
    health_checker=Depends(get_health_checker),
    rollout_provider=Depends(get_rollout_provider),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    k8s_api_factory=Depends(get_k8s_api_factory),
    agent_registry=Depends(get_agent_registry),
    user: User = Depends(get_current_user),
) -> dict:
    """批准一条待审批的高危操作(deploy / rollback / delete),建 task 执行原动作。

    批准者需 approval:{env}:approve 权限,且不能是发起人本人(§13 四眼原则)。批准后
    走与直接执行完全一致的编排路径:deploy/rollback 经 DeploymentService,delete 经
    LifecycleService,保证「批准执行」与「直接执行」无差异。
    """
    approval = await ApprovalRepository(session).get(approval_id)
    _require_approve_permission(user, approval.env)
    if approval.status != ApprovalStatus.PENDING:
        raise AppError("approval_not_pending", "该审批已决策,不能重复操作", status_code=409)
    if approval.requested_by == user.username:
        raise AppError("self_approval_forbidden", "不能批准自己发起的操作", status_code=403)

    task_type = _APPROVAL_TASK_TYPE.get(approval.action)
    if task_type is None:
        raise AppError(
            "approval_action_unsupported",
            f"暂不支持批准执行的动作: {approval.action.value}",
            status_code=501,
        )
    # deploy/rollback 需 CI 适配器;delete 走 SSH/agent 生命周期,不需要 provider。
    if approval.action in (ApprovalAction.DEPLOY, ApprovalAction.ROLLBACK) and provider is None:
        raise AppError("pipeline_not_configured", "未配置 CI 流水线,无法执行", status_code=501)

    payload = approval.payload or {}
    task = await TaskRepository(session).create(
        type=task_type,
        target=f"service:{approval.service_id}",
        payload={"env": approval.env, "approval_id": approval_id, **payload},
        created_by=user.username,
    )
    task_id = task.id
    await ApprovalRepository(session).approve(
        approval_id, decided_by=user.username, task_id=task_id
    )
    await AuditService(session).record(
        actor=user.username,
        action=f"service.{approval.action.value}.approved",
        target=f"service:{approval.service_id}",
        env=approval.env,
        result=AuditResult.SUCCESS,
        before={"requested_by": approval.requested_by},
        after={"approval_id": approval_id, "task_id": task_id, "payload": payload},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    operator = approval.requested_by or user.username
    if approval.action == ApprovalAction.DELETE:
        # delete 经生命周期服务(SSH/agent/k8s 多态),与直接删除同一路径。
        lifecycle = LifecycleService(
            db,
            secrets,
            connector=connector,
            k8s_api_factory=k8s_api_factory,
            agent_registry=agent_registry,
        )
        background.add_task(
            lifecycle.run_action,
            task_id=task_id,
            service_id=approval.service_id,
            action=TaskType.DELETE,
        )
    else:
        deployer = DeploymentService(
            db,
            adapter_provider=provider,
            health_checker=health_checker,
            rollout_provider=rollout_provider,
            auto_rollback_on_health_fail=request.app.state.settings.auto_rollback_on_health_fail,
        )
        if approval.action == ApprovalAction.ROLLBACK:
            background.add_task(
                deployer.run_rollback,
                task_id=task_id,
                service_id=approval.service_id,
                operator=operator,
            )
        else:
            strategy = DeploymentStrategy(payload.get("strategy", DeploymentStrategy.ROLLING.value))
            background.add_task(
                deployer.run_deploy,
                task_id=task_id,
                service_id=approval.service_id,
                request=DeployRequest(
                    version=payload.get("version", ""),
                    strategy=strategy,
                    git_sha=payload.get("git_sha"),
                ),
                operator=operator,
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
