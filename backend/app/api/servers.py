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
from app.schemas.server import ServerCreate, ServerOut, ServerRegisterRequest
from app.services.audit_service import AuditService
from app.services.monitoring_bootstrap import MonitoringBootstrapService
from app.services.prometheus_targets import PrometheusTargetRegistry
from app.services.server_repository import ServerRepository

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

    if body.access_mode == AccessMode.SSH:
        # 私钥进保险箱，业务表只留 credential_id（§13 严禁明文落库）
        credential_id = secrets.put(f"ssh-key:{body.name}", body.ssh_private_key or "")
        create = ServerCreate(
            name=body.name,
            host=body.host,
            access_mode=AccessMode.SSH,
            ssh_credential_id=credential_id,
            labels={
                **body.labels,
                "ssh_username": body.username or "root",
                "ssh_port": body.ssh_port,
            },
        )
    else:
        create = ServerCreate(
            name=body.name,
            host=body.host,
            access_mode=AccessMode.AGENT,
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

    # 纳管后自举监控:SSH 装 node_exporter 并登记 file_sd(§6.2 / T1.13)。
    # 走后台任务:自举耗时且可能失败,不阻塞也不拖垮纳管主流程。
    server_id = server.id
    bootstrap = MonitoringBootstrapService(
        db,
        secrets,
        registry=PrometheusTargetRegistry(settings.prometheus_targets_file),
        connector=connector,
        node_exporter_port=settings.node_exporter_port,
    )
    background.add_task(bootstrap.bootstrap_server, server_id)

    return ok(_server_out(server))


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

    labels = server.labels or {}
    target = SSHTarget(
        host=server.host,
        port=int(labels.get("ssh_port", 22)),
        username=str(labels.get("ssh_username", "root")),
        credential_id=server.ssh_credential_id or "",
    )
    connector = getattr(request.app.state, "ssh_connector", None)
    executor = SSHExecutor(target, secrets, connector=connector)
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
