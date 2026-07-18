"""服务器纳管 API(T1.6,设计 §3.2 接入管理)。

职责:
- POST /api/servers    纳管服务器:SSH 模式把私钥存进保险箱换 credential_id 再落库
                       （私钥绝不落业务表，§13）；写审计。
- GET  /api/servers    列出服务器（含 Agent/在线状态）。
- DELETE /api/servers/{id}  删除服务器（高危，走授权 + 审计）。
- POST /api/servers/{id}/test-connection  连通性测试（SSH 试连）。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.api.deps import (
    get_current_user,
    get_database,
    get_secret_store,
    get_session,
    get_ssh_connector,
    require_permission,
)
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import parse_permission
from app.core.responses import ok
from app.core.secrets import SecretStore
from app.models.audit import AuditResult
from app.models.server import AccessMode, Server
from app.models.user import User
from app.models.task import TaskType
from app.schemas.server import ServerCreate, ServerOut, ServerRegisterRequest
from app.schemas.task import TaskAccepted
from app.services.audit_service import AuditService
from app.services.agent_delivery_service import AgentDeliveryService
from app.services.environment_repository import EnvironmentRepository
from app.services.executor_factory import build_executor_for_server
from app.services.monitoring_bootstrap import MonitoringBootstrapService
from app.services.prometheus_targets import PrometheusTargetRegistry
from app.services.server_repository import ServerRepository
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _server_out(server: Server) -> dict:
    return ServerOut.model_validate(server).model_dump()


@router.post("", status_code=201)
async def register_server(
    body: ServerRegisterRequest,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    secrets: SecretStore = Depends(get_secret_store),
    db: Database = Depends(get_database),
    connector=Depends(get_ssh_connector),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_permission(parse_permission("server:*:write"))),
) -> dict:
    repo = ServerRepository(session)

    # 归属环境软校验(§10.1):environments 表须已存在该环境,否则 422。纳管时归类,
    # 保证服务器 environment 与自建环境一致,不产生悬空引用。
    env = await EnvironmentRepository(session).get_by_name(body.environment)
    if env is None:
        raise AppError(
            "environment_not_found",
            f"环境不存在: {body.environment}，请先在环境管理中创建",
            status_code=422,
        )

    if body.access_mode == AccessMode.SSH:
        # 私钥或密码进保险箱，业务表只留 credential_id（§13 严禁明文落库）。auth_type
        # 记入 labels,供 build_executor_for_server/连通性测试组装 SSHTarget 时区分。
        if body.auth_type == "password":
            credential_id = secrets.put(f"ssh-password:{body.name}", body.ssh_password or "")
        else:
            credential_id = secrets.put(f"ssh-key:{body.name}", body.ssh_private_key or "")
        create = ServerCreate(
            name=body.name,
            host=body.host,
            access_mode=AccessMode.SSH,
            environment=body.environment,
            ssh_credential_id=credential_id,
            labels={
                **body.labels,
                "ssh_username": body.username or "root",
                "ssh_port": body.ssh_port,
                "ssh_auth_type": body.auth_type,
            },
        )
    else:
        create = ServerCreate(
            name=body.name,
            host=body.host,
            access_mode=AccessMode.AGENT,
            environment=body.environment,
            agent_id=body.agent_id,
            labels=body.labels,
        )

    server = await repo.create(create)
    await AuditService(session).record(
        actor=user.username,
        action="server.register",
        target=f"server:{server.id}",
        result=AuditResult.SUCCESS,
        after={"name": server.name, "host": server.host, "access_mode": server.access_mode.value},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    # 纳管行必须在调度后台自举前落库:BackgroundTasks 在响应阶段执行,早于
    # get_session 依赖的延后提交;而 bootstrap_server 另起独立会话按 id 读服务器,
    # 若此处不先提交,后台会查不到刚建的行并上抛异常,连带回滚整笔纳管(§6.2)。
    payload = _server_out(server)
    server_id = server.id
    await session.commit()

    # 纳管后自举监控:SSH 装 node_exporter 并登记 file_sd(§6.2 / T1.13)。
    # 走后台任务:自举耗时且可能失败,不阻塞也不拖垮纳管主流程。
    bootstrap = MonitoringBootstrapService(
        db,
        secrets,
        registry=PrometheusTargetRegistry(settings.prometheus_targets_file),
        connector=connector,
        node_exporter_port=settings.node_exporter_port,
        node_exporter_version=settings.node_exporter_version,
        node_exporter_base_url=settings.control_plane_base_url,
    )
    background.add_task(bootstrap.bootstrap_server, server_id)

    return ok(payload)


@router.get("")
async def list_servers(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    repo = ServerRepository(session)
    servers = await repo.list()
    return ok([_server_out(s) for s in servers])


@router.post("/{server_id}/test-connection")
async def test_connection(
    server_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    secrets: SecretStore = Depends(get_secret_store),
    _: User = Depends(get_current_user),
) -> dict:
    repo = ServerRepository(session)
    server = await repo.get(server_id)

    if server.access_mode != AccessMode.SSH:
        raise AppError(
            "connectivity_unsupported",
            "仅 SSH 模式支持连通性测试；Agent 模式以心跳为准",
            status_code=400,
        )

    # 复用共享工厂组装 Executor:auth_type(key/password)从 labels 读出,连通性测试
    # 与生命周期/配置下发走完全一致的建连逻辑,不再在此重复组装 SSHTarget(避免漂移)。
    connector = getattr(request.app.state, "ssh_connector", None)
    executor = build_executor_for_server(server, secrets, connector=connector)
    if not isinstance(executor, SSHExecutor):
        raise AppError(
            "connectivity_unsupported",
            "仅 SSH 模式支持连通性测试；Agent 模式以心跳为准",
            status_code=400,
        )
    reachable = await executor.test_connectivity()
    return ok({"reachable": reachable})


@router.delete("/{server_id}")
async def delete_server(
    server_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_permission(parse_permission("server:*:delete"))),
) -> dict:
    repo = ServerRepository(session)
    server = await repo.get(server_id)
    before = {"name": server.name, "host": server.host, "access_mode": server.access_mode.value}
    await repo.delete(server_id)
    await AuditService(session).record(
        actor=user.username,
        action="server.delete",
        target=f"server:{server_id}",
        result=AuditResult.SUCCESS,
        before=before,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok({"deleted": True})


@router.post("/{server_id}/install-agent", status_code=202)
async def install_agent(
    server_id: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    secrets: SecretStore = Depends(get_secret_store),
    db: Database = Depends(get_database),
    connector=Depends(get_ssh_connector),
    user: User = Depends(require_permission(parse_permission("server:*:write"))),
) -> dict:
    """对 SSH 纳管的服务器经 SSH 下发安装 axon-agent(需求4)。

    落一条 agent_install task,后台经 SSHExecutor 跑安装脚本(二进制从控制面下载
    端点拉取,离线分发),前端轮询 task 终态。非 SSH 服务器由编排层落 task.failed。
    """
    repo = ServerRepository(session)
    server = await repo.get(server_id)

    if server.access_mode != AccessMode.SSH:
        raise AppError(
            "agent_install_requires_ssh",
            "仅 SSH 接入的服务器支持经 SSH 下发 Agent",
            status_code=400,
        )

    task = await TaskRepository(session).create(
        type=TaskType.AGENT_INSTALL,
        target=f"server:{server_id}",
        payload={"host": server.host},
        created_by=user.username,
    )
    task_id = task.id
    await AuditService(session).record(
        actor=user.username,
        action="server.install_agent",
        target=f"server:{server_id}",
        result=AuditResult.SUCCESS,
        after={"task_id": task_id},
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )

    delivery = AgentDeliveryService(
        db,
        secrets,
        request.app.state.settings,
        connector=connector,
    )
    background.add_task(delivery.run_install, task_id=task_id, server_id=server_id)

    accepted = TaskAccepted(task_id=task_id, status=task.status)
    return ok(accepted.model_dump())
