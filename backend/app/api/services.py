"""服务生命周期 API(T1.10,设计 §15.2 / §10.2)。

四端点 start/stop/restart/delete:每次落一条 task 并交由 BackgroundTasks 异步
执行,立即返回 202 + task_id,前端据此轮询(T1.11)或订阅推送(T0.10)。

鉴权按 service.env 动态判定(§10.2 各环境差异化管控):动作到权限的映射见
_ACTION_PERMISSION——start/stop/restart 归为 operate,delete 为高危 delete。
因权限点依赖运行时加载出的 env(静态 require_permission 依赖无法表达),故在
路由内加载 service 后内联校验。所有动作写审计(§14.7)。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_database,
    get_health_checker,
    get_pipeline_adapter_provider,
    get_secret_store,
    get_session,
    get_ssh_connector,
)
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.responses import ok
from app.core.secrets import SecretStore
from app.models.approval import ApprovalAction
from app.models.audit import AuditResult
from app.models.service import Runtime, Service, ServiceEnvironment
from app.models.task import TaskStatus, TaskType
from app.models.user import User
from app.schemas.deployment import DeploymentOut
from app.schemas.scan import ScanResultOut
from app.schemas.service import (
    DeployRequestBody,
    PromoteRequestBody,
    ServiceCreate,
    ServiceOut,
)
from app.schemas.service_config import (
    ConfigDeliveryOut,
    ConfigVersionCreate,
    ConfigVersionOut,
)
from app.schemas.task import TaskAccepted
from app.services.approval_repository import ApprovalRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.config_delivery_repository import ConfigDeliveryRepository
from app.services.config_delivery_service import ConfigDeliveryService
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService, DeployRequest
from app.services.executor_factory import build_executor_for_server
from app.services.lifecycle_service import LifecycleService
from app.services.quality_gate import check_quality_gate
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_config_repository import ServiceConfigRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api/services", tags=["services"])

# 生命周期动作 → 该动作要求的权限 action 段。start/stop/restart 属常规运维
# (operate);delete 为高危(delete),配合 env 段实现 prod 严格管控(§10.2)。
_OPERATE = "operate"
_ACTION_PERMISSION: dict[TaskType, str] = {
    TaskType.START: _OPERATE,
    TaskType.STOP: _OPERATE,
    TaskType.RESTART: _OPERATE,
    TaskType.DELETE: "delete",
    TaskType.DEPLOY: "deploy",
}


def _require_service_permission(user: User, service: Service, action: TaskType) -> None:
    """按 service.env 校验用户是否有权对该服务执行此动作,无权抛 403。"""
    required = Permission(
        resource="service",
        env=service.env.value,
        action=_ACTION_PERMISSION[action],
    )
    if not AuthService.permission_set(user).allows(required):
        raise AppError("forbidden", f"缺少权限: {required}", status_code=403)


def _require_write_permission(user: User, env: ServiceEnvironment) -> None:
    """创建服务按目标环境校验 service:{env}:write,无权抛 403。"""
    required = Permission(resource="service", env=env.value, action="write")
    if not AuthService.permission_set(user).allows(required):
        raise AppError("forbidden", f"缺少权限: {required}", status_code=403)


def _require_config_permission(user: User, service: Service) -> None:
    """配置的写/切版按 service.env 校验 operate,与生命周期常规运维同档(§10.2)。"""
    required = Permission(resource="service", env=service.env.value, action=_OPERATE)
    if not AuthService.permission_set(user).allows(required):
        raise AppError("forbidden", f"缺少权限: {required}", status_code=403)


def _service_out(service: Service) -> dict:
    """把 Service(已预加载 placements)转为列表/详情视图。"""
    view = ServiceOut.model_validate(service).model_copy(
        update={"placement_count": len(service.placements)}
    )
    return view.model_dump()


@router.get("")
async def list_services(
    env: ServiceEnvironment | None = Query(default=None, description="按环境过滤"),
    runtime: Runtime | None = Query(default=None, description="按运行时过滤"),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    services = await ServiceRepository(session).list_services(env=env, runtime=runtime)
    return ok([_service_out(s) for s in services])


@router.post("", status_code=201)
async def create_service(
    body: ServiceCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    _require_write_permission(user, body.env)
    repo = ServiceRepository(session)
    service = await repo.create_service(body)
    await AuditService(session).record(
        actor=user.username,
        action="service.create",
        target=f"service:{service.id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"name": service.name, "runtime": service.runtime.value},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    # 新建服务尚无 placement,计数为 0;避免触发未加载关系的惰性访问
    view = ServiceOut.model_validate(service).model_copy(update={"placement_count": 0})
    return ok(view.model_dump())


async def _accept_action(
    *,
    service_id: str,
    action: TaskType,
    request: Request,
    session: AsyncSession,
    db: Database,
    secrets: SecretStore,
    connector,
    background: BackgroundTasks,
    user: User,
) -> dict:
    """受理一次生命周期动作:加载服务→鉴权→建 task→写审计→调度异步执行。

    task 与审计在请求会话内落库(请求结束即提交,先于后台任务运行),后台任务
    另起会话流转状态,避免读到未提交的 task。
    """
    service = await ServiceRepository(session).get_service(service_id)
    _require_service_permission(user, service, action)

    task = await TaskRepository(session).create(
        type=action,
        target=f"service:{service_id}",
        payload={"env": service.env.value, "runtime": service.runtime.value},
        created_by=user.username,
    )
    task_id = task.id

    await AuditService(session).record(
        actor=user.username,
        action=f"service.{action.value}",
        target=f"service:{service_id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id, "action": action.value},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    lifecycle = LifecycleService(db, secrets, connector=connector)
    background.add_task(lifecycle.run_action, task_id=task_id, service_id=service_id, action=action)

    accepted = TaskAccepted(task_id=task_id, status=task.status)
    return ok(accepted.model_dump())


async def _handle(
    service_id: str,
    action: TaskType,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession,
    db: Database,
    secrets: SecretStore,
    connector,
    user: User,
) -> dict:
    return await _accept_action(
        service_id=service_id,
        action=action,
        request=request,
        session=session,
        db=db,
        secrets=secrets,
        connector=connector,
        background=background,
        user=user,
    )


@router.post("/{service_id}/start", status_code=202)
async def start_service(
    service_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    user: User = Depends(get_current_user),
) -> dict:
    return await _handle(
        service_id, TaskType.START, request, background, session, db, secrets, connector, user
    )


@router.post("/{service_id}/stop", status_code=202)
async def stop_service(
    service_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    user: User = Depends(get_current_user),
) -> dict:
    return await _handle(
        service_id, TaskType.STOP, request, background, session, db, secrets, connector, user
    )


@router.post("/{service_id}/restart", status_code=202)
async def restart_service(
    service_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    user: User = Depends(get_current_user),
) -> dict:
    return await _handle(
        service_id, TaskType.RESTART, request, background, session, db, secrets, connector, user
    )


@router.delete("/{service_id}", status_code=202)
async def delete_service(
    service_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    user: User = Depends(get_current_user),
) -> dict:
    return await _handle(
        service_id, TaskType.DELETE, request, background, session, db, secrets, connector, user
    )


async def _start_deploy(
    *,
    service: Service,
    body: DeployRequestBody,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession,
    db: Database,
    provider,
    operator: str,
    health_checker=None,
) -> str:
    """建 deploy task + 写审计 + 调度异步部署编排,返回 task_id。

    直接部署(dev/staging 或未开审批的 prod)与审批通过后的执行共用此逻辑,
    保证「批准后执行」与「直接执行」走完全一致的部署路径。
    """
    task = await TaskRepository(session).create(
        type=TaskType.DEPLOY,
        target=f"service:{service.id}",
        payload={"env": service.env.value, "version": body.version},
        created_by=operator,
    )
    task_id = task.id
    await AuditService(session).record(
        actor=operator,
        action="service.deploy",
        target=f"service:{service.id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id, "version": body.version, "strategy": body.strategy.value},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    deployer = DeploymentService(db, adapter_provider=provider, health_checker=health_checker)
    background.add_task(
        deployer.run_deploy,
        task_id=task_id,
        service_id=service.id,
        request=DeployRequest(version=body.version, strategy=body.strategy, git_sha=body.git_sha),
        operator=operator,
    )
    return task_id


@router.post("/{service_id}/deploy", status_code=202)
async def deploy_service(
    service_id: str,
    body: DeployRequestBody,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    provider=Depends(get_pipeline_adapter_provider),
    health_checker=Depends(get_health_checker),
    user: User = Depends(get_current_user),
) -> dict:
    """UI 触发部署(§8.1 模式 A):落 deploy task 与 deployment 记录,异步调 CI。

    鉴权按 service.env 判 service:{env}:deploy。task 与审计在请求会话内落库
    (先于后台任务运行),后台任务另起会话跑 DeploymentService 编排。
    """
    if provider is None:
        raise AppError(
            "pipeline_not_configured",
            "未配置 CI 流水线,无法触发部署",
            status_code=501,
        )

    service = await ServiceRepository(session).get_service(service_id)
    _require_service_permission(user, service, TaskType.DEPLOY)

    # 部署前质量门禁(§7.2):带 git_sha 且策略开启时,存在 critical 拦截(422)
    await check_quality_gate(
        ScanResultRepository(session),
        git_sha=body.git_sha,
        block_on_critical=request.app.state.settings.deploy_block_on_critical,
    )

    # 生产审批流(§10.2/§13):prod 高危操作在开关开启时不直接执行,先落 pending
    # 审批,返回审批 id;具 approval 权限者批准后才建 task 执行(见 approvals API)。
    settings = request.app.state.settings
    if service.env == ServiceEnvironment.PROD and settings.require_prod_approval:
        approval = await ApprovalRepository(session).create(
            service_id=service_id,
            env=service.env.value,
            action=ApprovalAction.DEPLOY,
            payload={
                "version": body.version,
                "strategy": body.strategy.value,
                "git_sha": body.git_sha,
            },
            requested_by=user.username,
        )
        await AuditService(session).record(
            actor=user.username,
            action="service.deploy.request_approval",
            target=f"service:{service_id}",
            env=service.env.value,
            result=AuditResult.SUCCESS,
            after={"approval_id": approval.id, "version": body.version},
            ip=request.client.host if request.client else None,
            ua=request.headers.get("user-agent"),
        )
        return ok(
            {"approval_id": approval.id, "status": approval.status.value, "pending_approval": True}
        )

    task_id = await _start_deploy(
        service=service,
        body=body,
        request=request,
        background=background,
        session=session,
        db=db,
        provider=provider,
        operator=user.username,
        health_checker=health_checker,
    )
    accepted = TaskAccepted(task_id=task_id, status=TaskStatus.PENDING)
    return ok(accepted.model_dump())


@router.post("/{service_id}/rollback", status_code=202)
async def rollback_service(
    service_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    provider=Depends(get_pipeline_adapter_provider),
    health_checker=Depends(get_health_checker),
    user: User = Depends(get_current_user),
) -> dict:
    """一键回滚(§11.1):重部署上一版制品。与 deploy 同权限点,异步落 ROLLBACK task。"""
    service = await ServiceRepository(session).get_service(service_id)
    _require_service_permission(user, service, TaskType.DEPLOY)
    if provider is None:
        raise AppError("pipeline_not_configured", "未配置 CI 适配器,无法触发回滚", status_code=503)

    task = await TaskRepository(session).create(
        type=TaskType.ROLLBACK,
        target=f"service:{service_id}",
        payload={"env": service.env.value},
        created_by=user.username,
    )
    task_id = task.id
    await AuditService(session).record(
        actor=user.username,
        action="service.rollback",
        target=f"service:{service_id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    deployer = DeploymentService(db, adapter_provider=provider, health_checker=health_checker)
    background.add_task(
        deployer.run_rollback,
        task_id=task_id,
        service_id=service_id,
        operator=user.username,
    )

    accepted = TaskAccepted(task_id=task_id, status=task.status)
    return ok(accepted.model_dump())


@router.post("/{service_id}/promote", status_code=202)
async def promote_service(
    service_id: str,
    body: PromoteRequestBody,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    provider=Depends(get_pipeline_adapter_provider),
    health_checker=Depends(get_health_checker),
    user: User = Depends(get_current_user),
) -> dict:
    """环境晋升(§10.3):取源服务(如 staging)最近成功部署的同一制品,在本
    目标服务(如 prod)重新部署,不重构建。异步落 DEPLOY task。

    鉴权按目标 service.env 判 operate:晋升到 prod 需 prod 部署权限。源与目标
    须同名不同 env(晋升是跨环境搬同一制品,不是任意服务间拷贝)。
    """
    if provider is None:
        raise AppError("pipeline_not_configured", "未配置 CI 流水线,无法晋升", status_code=501)

    target = await ServiceRepository(session).get_service(service_id)
    _require_service_permission(user, target, TaskType.DEPLOY)

    source = await ServiceRepository(session).get_service(body.source_service_id)
    if source.name != target.name:
        raise AppError(
            "promote_service_mismatch",
            "晋升的源与目标必须是同一服务的不同环境",
            status_code=400,
        )
    if source.env == target.env:
        raise AppError(
            "promote_same_env", "晋升的源与目标环境不能相同", status_code=400
        )

    task = await TaskRepository(session).create(
        type=TaskType.DEPLOY,
        target=f"service:{service_id}",
        payload={"env": target.env.value, "promote_from": body.source_service_id},
        created_by=user.username,
    )
    task_id = task.id
    await AuditService(session).record(
        actor=user.username,
        action="service.promote",
        target=f"service:{service_id}",
        env=target.env.value,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id, "source_service_id": body.source_service_id},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    deployer = DeploymentService(db, adapter_provider=provider, health_checker=health_checker)
    background.add_task(
        deployer.run_promotion,
        task_id=task_id,
        source_service_id=body.source_service_id,
        target_service_id=service_id,
        operator=user.username,
    )

    accepted = TaskAccepted(task_id=task_id, status=task.status)
    return ok(accepted.model_dump())


@router.get("/{service_id}/deployments")
async def list_deployments(
    service_id: str,
    env: ServiceEnvironment | None = Query(default=None, description="按环境过滤"),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """服务的部署历史(最新在前),供部署页与主页 feed。"""
    rows = await DeploymentRepository(session).list_for_service(
        service_id, env=env.value if env else None
    )
    return ok([DeploymentOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.get("/{service_id}/deployments/{deployment_id}")
async def get_deployment_detail(
    service_id: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """单次部署详情 + 按 git_sha 关联的扫描结论(§7.2/§14.8 全链路关联)。

    实现"点开一次部署,看到这次上线扫描过没有、有没有高危"。deployment 不属于
    该 service 或不存在均 404。git_sha 为空时 scans 为空列表。
    """
    dep = await DeploymentRepository(session).get(deployment_id)
    if dep.service_id != service_id:
        raise AppError("deployment_not_found", "部署记录不存在", status_code=404)

    scans: list[dict] = []
    if dep.git_sha:
        rows = await ScanResultRepository(session).list_for_git_sha(dep.git_sha)
        scans = [ScanResultOut.model_validate(r).model_dump(mode="json") for r in rows]

    return ok(
        {
            "deployment": DeploymentOut.model_validate(dep).model_dump(mode="json"),
            "scans": scans,
        }
    )


def _config_out(config) -> dict:
    return ConfigVersionOut.model_validate(config).model_dump(mode="json")


@router.get("/{service_id}/configs")
async def list_config_versions(
    service_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """配置版本历史(§15.3),最新在前;服务不存在 404。"""
    await ServiceRepository(session).get_service(service_id)
    rows = await ServiceConfigRepository(session).list_versions(service_id)
    return ok([_config_out(r) for r in rows])


@router.get("/{service_id}/configs/current")
async def get_current_config(
    service_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """当前生效配置版本(§15.3);无版本返回 null;服务不存在 404。"""
    await ServiceRepository(session).get_service(service_id)
    current = await ServiceConfigRepository(session).get_current(service_id)
    return ok(_config_out(current) if current is not None else None)


@router.post("/{service_id}/configs", status_code=201)
async def create_config_version(
    service_id: str,
    body: ConfigVersionCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """新建配置版本(§15.3 暂存),新版自动成为 current。按 service.env 鉴权 operate。"""
    service = await ServiceRepository(session).get_service(service_id)
    _require_config_permission(user, service)

    config = await ServiceConfigRepository(session).create_version(
        service_id=service_id,
        content=body.content,
        format=body.format,
        created_by=user.username,
        comment=body.comment,
        target_path=body.target_path,
    )
    await AuditService(session).record(
        actor=user.username,
        action="service.config.create",
        target=f"service:{service_id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"version": config.version},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(_config_out(config))


@router.post("/{service_id}/configs/{version}/activate")
async def activate_config_version(
    service_id: str,
    version: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """切换生效版本(§15.3 配置回滚)。按 service.env 鉴权 operate;目标版不存在 404。"""
    service = await ServiceRepository(session).get_service(service_id)
    _require_config_permission(user, service)

    config = await ServiceConfigRepository(session).activate(service_id, version)
    await AuditService(session).record(
        actor=user.username,
        action="service.config.activate",
        target=f"service:{service_id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"version": config.version},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(_config_out(config))


@router.post("/{service_id}/configs/{version}/apply", status_code=202)
async def apply_config_version(
    service_id: str,
    version: int,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    connector=Depends(get_ssh_connector),
    user: User = Depends(get_current_user),
) -> dict:
    """下发指定配置版本到该服务各放置点(§15.3)。异步落 UPDATE_CONFIG task,逐目标
    写 config_deliveries;返回 task_id 供前端轮询。按 service.env 鉴权 operate。"""
    service = await ServiceRepository(session).get_service(service_id)
    _require_config_permission(user, service)
    # 版本不存在直接 404(不建 task)
    config = await ServiceConfigRepository(session).get_version(service_id, version)

    task = await TaskRepository(session).create(
        type=TaskType.UPDATE_CONFIG,
        target=f"service:{service_id}",
        payload={"env": service.env.value, "config_version": version},
        created_by=user.username,
    )
    task_id = task.id
    config_id = config.id
    await AuditService(session).record(
        actor=user.username,
        action="service.config.apply",
        target=f"service:{service_id}",
        env=service.env.value,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id, "version": version},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    delivery = ConfigDeliveryService(
        db,
        secrets,
        executor_builder=lambda server: build_executor_for_server(
            server, secrets, connector=connector
        ),
    )
    background.add_task(
        delivery.run_delivery,
        task_id=task_id,
        config_id=config_id,
        operator=user.username,
    )

    accepted = TaskAccepted(task_id=task_id, status=task.status)
    return ok(accepted.model_dump())


@router.get("/{service_id}/configs/{version}/deliveries")
async def list_config_deliveries(
    service_id: str,
    version: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    """某配置版本的逐目标下发结果(§14.5),供下发页展示部分成功/失败;版本不存在 404。"""
    config = await ServiceConfigRepository(session).get_version(service_id, version)
    rows = await ConfigDeliveryRepository(session).list_for_config(config.id)
    return ok([ConfigDeliveryOut.model_validate(r).model_dump(mode="json") for r in rows])
