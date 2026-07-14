"""Agent 经 SSH 下发安装的编排服务(需求4)。

对一台 SSH 纳管的服务器,经 SSHExecutor 跑 AgentInstaller 安装脚本,并驱动
agent_install task 的状态机(running → success / failed)。与 LifecycleService
同构:状态分段提交(先标 running 让轮询可见,执行完另起会话落终态),全程不抛,
结果落在 task 上。

二进制走离线分发:download_url = control_plane_base_url + /api/dist/ + 文件名,
由本服务按 settings 组装并注入安装脚本,不走公网 github(§离线分发决策)。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.agent_installer import AgentInstaller
from app.adapters.executor import Executor
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.server import AccessMode, Server
from app.models.task import TaskStatus
from app.services.executor_factory import build_executor_for_server
from app.services.server_repository import ServerRepository
from app.services.task_repository import TaskRepository

log = get_logger("agent_delivery")


def agent_download_url(settings: Settings) -> str:
    """按 settings 组装 axon-agent 的控制面下载 URL(离线分发)。"""
    base = settings.control_plane_base_url.rstrip("/")
    filename = f"{settings.agent_service_name}-{settings.agent_version}-linux-amd64"
    return f"{base}/api/dist/{filename}"


class AgentDeliveryService:
    """经 SSH 下发安装 axon-agent,驱动 task 状态机。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        settings: Settings,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._settings = settings
        self._connector = connector

    async def run_install(self, *, task_id: str, server_id: str) -> None:
        """执行一次 Agent 下发安装。全程不抛:结果落在 task 状态上。"""
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            await self._install(server_id)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("agent_install_failed", server_id=server_id, error=message)
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(
                    task_id, TaskStatus.FAILED, error=message
                )
            return

        async with self._db.session() as session:
            await TaskRepository(session).mark_result(
                task_id, TaskStatus.SUCCESS, result={"action": "agent_install"}
            )

    async def _install(self, server_id: str) -> None:
        """加载服务器→构造 SSH executor→跑安装脚本。仅 SSH 模式;任一步失败即抛。"""
        async with self._db.session() as session:
            server = await ServerRepository(session).get(server_id)
            self._require_ssh(server)
            executor = self._build_executor(server)

        installer = AgentInstaller(executor)
        await installer.ensure_installed(
            download_url=agent_download_url(self._settings),
            version=self._settings.agent_version,
            install_dir=self._settings.agent_install_dir,
            service_name=self._settings.agent_service_name,
        )

    @staticmethod
    def _require_ssh(server: Server) -> None:
        if server.access_mode != AccessMode.SSH:
            raise AppError(
                "agent_install_unsupported",
                "仅 SSH 纳管的服务器支持经 SSH 下发安装 Agent",
                status_code=400,
            )

    def _build_executor(self, server: Server) -> Executor:
        return build_executor_for_server(server, self._secrets, connector=self._connector)
