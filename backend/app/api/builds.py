"""构建能力 API(构建能力一期,方案 A 本地构建)。

端点:
- POST   /api/services/{id}/build     触发本地构建(202 + task,前端轮询)。
- GET    /api/services/{id}/builds    该服务构建历史(最新在前)。
- GET    /api/services/{id}/artifacts 该服务构建产物列表。
- GET    /api/builds/{id}             单条构建详情。
- GET    /api/build-nodes             构建节点列表。
- POST   /api/build-nodes             注册本地/SSH 构建节点(buildnode:*:write)。
- DELETE /api/build-nodes/{id}        删除构建节点(buildnode:*:write)。
- GET    /api/artifact-registries     制品库列表。
- POST   /api/artifact-registries     建制品库(凭据换 vault id,buildnode:*:write)。
- DELETE /api/artifact-registries/{id} 删制品库(buildnode:*:write)。

触发构建鉴权按 service.env 判 service:{env}:deploy(与部署同档,构建是部署前置)。
task 与审计在请求会话内落库(先于后台任务运行),后台另起会话跑 BuildService。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_database,
    get_secret_store,
    get_session,
    require_permission,
)
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission, parse_permission
from app.core.responses import ok
from app.core.secrets import SecretStore
from app.models.audit import AuditResult
from app.models.build import BuildSource
from app.models.service import Service
from app.models.task import TaskType
from app.models.user import User
from app.schemas.build import (
    ArtifactOut,
    ArtifactRegistryCreate,
    ArtifactRegistryOut,
    BuildNodeCreate,
    BuildNodeOut,
    BuildOut,
    BuildRequestBody,
)
from app.schemas.task import TaskAccepted
from app.services.artifact_repository import ArtifactRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.build_node_repository import BuildNodeRepository
from app.services.build_repository import BuildRepository
from app.services.build_service import BuildService
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api", tags=["builds"])


def _require_build_permission(user: User, service: Service) -> None:
    """构建按 service.env 校验 service:{env}:deploy(构建是部署前置,同档管控)。"""
    required = Permission(resource="service", env=service.env, action="deploy")
    if not AuthService.permission_set(user).allows(required):
        raise AppError("forbidden", f"缺少权限: {required}", status_code=403)


def _build_service(request: Request, db: Database, secrets: SecretStore) -> BuildService:
    """构造编排服务。测试可经 app.state.build_executor_factory 注入 fake 执行器。"""
    factory = getattr(request.app.state, "build_executor_factory", None)
    return BuildService(
        db,
        secrets,
        request.app.state.settings,
        executor_factory=factory,
        redis=getattr(request.app.state, "redis", None),
        connector=getattr(request.app.state, "ssh_connector", None),
    )


# ── 构建触发 / 查询 ────────────────────────────────────────────────


@router.post("/services/{service_id}/build", status_code=202)
async def trigger_build(
    service_id: str,
    body: BuildRequestBody,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    db: Database = Depends(get_database),
    secrets: SecretStore = Depends(get_secret_store),
    user: User = Depends(get_current_user),
) -> dict:
    """触发一次控制面本地构建(方案 A)。未配 build_config 的服务拒绝(501)。"""
    service = await ServiceRepository(session).get_service(service_id)
    _require_build_permission(user, service)

    if not service.build_config:
        raise AppError(
            "build_not_configured",
            "服务未配置构建(build_config 为空),无法本地构建",
            status_code=501,
        )

    build_config = dict(service.build_config)
    git_ref = body.git_ref or build_config.get("git_ref")
    version = body.version or build_config.get("version")

    build = await BuildRepository(session).create(
        service_id=service_id,
        source=BuildSource.UI_TRIGGERED,
        repo_url=build_config.get("repo_url"),
        git_ref=git_ref,
        version=version,
        operator=user.username,
    )
    build_id = build.id
    task = await TaskRepository(session).create(
        type=TaskType.BUILD,
        target=f"service:{service_id}",
        payload={"build_id": build_id, "git_ref": git_ref},
        created_by=user.username,
    )
    task_id = task.id
    await AuditService(session).record(
        actor=user.username,
        action="service.build",
        target=f"service:{service_id}",
        env=service.env,
        result=AuditResult.SUCCESS,
        after={"task_id": task_id, "build_id": build_id, "git_ref": git_ref},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    builder = _build_service(request, db, secrets)
    background.add_task(
        builder.run_build, task_id=task_id, build_id=build_id, service_id=service_id
    )
    return ok(TaskAccepted(task_id=task_id, status=task.status).model_dump())


@router.get("/services/{service_id}/builds")
async def list_service_builds(
    service_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await BuildRepository(session).list_for_service(service_id, limit=limit)
    return ok([BuildOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.get("/services/{service_id}/artifacts")
async def list_service_artifacts(
    service_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await ArtifactRepository(session).list_for_service(service_id, limit=limit)
    return ok([ArtifactOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.get("/builds/{build_id}")
async def get_build(
    build_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    build = await BuildRepository(session).get(build_id)
    return ok(BuildOut.model_validate(build).model_dump(mode="json"))


# ── 构建节点 ───────────────────────────────────────────────────────


@router.get("/build-nodes")
async def list_build_nodes(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await BuildNodeRepository(session).list()
    return ok([BuildNodeOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.post("/build-nodes", status_code=201)
async def create_build_node(
    body: BuildNodeCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("buildnode:*:write"))),
) -> dict:
    node = await BuildNodeRepository(session).create(
        name=body.name,
        server_id=body.server_id,
        host=body.host,
        ssh_credential_id=body.ssh_credential_id,
        labels=body.labels,
        max_concurrent=body.max_concurrent,
    )
    await AuditService(session).record(
        actor=user.username,
        action="build_node.create",
        target=f"build_node:{node.id}",
        result=AuditResult.SUCCESS,
        after={"name": node.name, "server_id": node.server_id},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(BuildNodeOut.model_validate(node).model_dump(mode="json"))


@router.delete("/build-nodes/{node_id}")
async def delete_build_node(
    node_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("buildnode:*:write"))),
) -> dict:
    repo = BuildNodeRepository(session)
    node = await repo.get(node_id)
    before = {"name": node.name}
    await repo.delete(node_id)
    await AuditService(session).record(
        actor=user.username,
        action="build_node.delete",
        target=f"build_node:{node_id}",
        result=AuditResult.SUCCESS,
        before=before,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})


@router.post("/build-nodes/{node_id}/heartbeat")
async def heartbeat_build_node(
    node_id: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_permission(parse_permission("buildnode:*:write"))),
) -> dict:
    node = await BuildNodeRepository(session).mark_heartbeat(node_id)
    return ok(BuildNodeOut.model_validate(node).model_dump(mode="json"))


# ── 制品库 ─────────────────────────────────────────────────────────


@router.get("/artifact-registries")
async def list_artifact_registries(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    rows = await ArtifactRepository(session).list_registries()
    return ok([ArtifactRegistryOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.post("/artifact-registries", status_code=201)
async def create_artifact_registry(
    body: ArtifactRegistryCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    secrets: SecretStore = Depends(get_secret_store),
    user: User = Depends(require_permission(parse_permission("buildnode:*:write"))),
) -> dict:
    # 凭据进保险箱换 credential_id,业务表只留引用(§13,规矩同 servers)。
    credential_id = None
    if body.credential:
        credential_id = secrets.put(f"registry:{body.name}", body.credential)
    registry = await ArtifactRepository(session).create_registry(
        name=body.name,
        type_=body.type,
        url=body.url,
        credential_id=credential_id,
        description=body.description,
    )
    await AuditService(session).record(
        actor=user.username,
        action="artifact_registry.create",
        target=f"artifact_registry:{registry.id}",
        result=AuditResult.SUCCESS,
        after={"name": registry.name, "type": registry.type.value},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok(ArtifactRegistryOut.model_validate(registry).model_dump(mode="json"))


@router.delete("/artifact-registries/{registry_id}")
async def delete_artifact_registry(
    registry_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("buildnode:*:write"))),
) -> dict:
    repo = ArtifactRepository(session)
    registry = await repo.get_registry(registry_id)
    before = {"name": registry.name}
    await repo.delete_registry(registry_id)
    await AuditService(session).record(
        actor=user.username,
        action="artifact_registry.delete",
        target=f"artifact_registry:{registry_id}",
        result=AuditResult.SUCCESS,
        before=before,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})
