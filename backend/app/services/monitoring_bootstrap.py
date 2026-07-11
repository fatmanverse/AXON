"""监控自举编排(T1.13,设计 §6.2)。

把「装 node_exporter」与「登记 Prometheus file_sd 目标」两步编排成一次服务器
纳管后的自举动作:对 SSH 服务器经 SSHExecutor 安装 node_exporter,成功后写入
file_sd,Prometheus 按 refresh_interval 自动发现新目标。

设计要点:
- 只对 SSH 服务器自举;Agent 模式由 Agent 自身自举 node_exporter(§5.2),此处跳过。
- 安装失败不登记目标——避免 Prometheus 抓取装不成功的机器;失败以返回值表达,
  不抛异常,便于作为纳管后的后台任务运行而不拖垮纳管主流程。
- installer 与 registry 均可注入,便于单测隔离真实 SSH/文件系统。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.node_exporter import DEFAULT_PORT, NodeExporterInstaller
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.db import Database
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.server import AccessMode
from app.services.prometheus_targets import PrometheusTargetRegistry
from app.services.server_repository import ServerRepository

log = get_logger("monitoring_bootstrap")


@dataclass(frozen=True)
class BootstrapResult:
    """一次服务器自举的结果:是否跳过、是否装成功、登记的抓取目标。"""

    skipped: bool
    installed: bool
    target: str | None = None


class MonitoringBootstrapService:
    """编排 node_exporter 安装 + file_sd 目标登记。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        registry: PrometheusTargetRegistry,
        connector: Callable[..., Any] | None = None,
        node_exporter_port: int = DEFAULT_PORT,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._registry = registry
        self._connector = connector
        self._port = node_exporter_port

    async def bootstrap_server(self, server_id: str) -> BootstrapResult:
        """对一台服务器自举监控。SSH 装 node_exporter 成功后登记抓取目标。"""
        async with self._db.session() as session:
            server = await ServerRepository(session).get(server_id)
            access_mode = server.access_mode
            host = server.host
            labels = dict(server.labels or {})
            credential_id = server.ssh_credential_id
            name = server.name

        if access_mode != AccessMode.SSH:
            # Agent 模式由 Agent 自举 node_exporter(§5.2),控制面不经 SSH 介入
            log.info("monitoring_bootstrap_skip_agent", server_id=server_id)
            return BootstrapResult(skipped=True, installed=False)

        installed = await self._install(host, labels, credential_id)
        if not installed:
            return BootstrapResult(skipped=False, installed=False)

        target = f"{host}:{self._port}"
        try:
            self._registry.register(
                host=host,
                port=self._port,
                labels={"server_id": server_id, "server_name": name},
            )
        except Exception as exc:
            # 装成功但写 file_sd 失败(路径无权限等):记录后不抛。作为纳管后的
            # fire-and-forget 后台任务,绝不能因目标登记失败而崩溃。
            log.warning(
                "monitoring_bootstrap_register_failed",
                server_id=server_id,
                target=target,
                error_type=type(exc).__name__,
            )
            return BootstrapResult(skipped=False, installed=True, target=None)
        log.info("monitoring_bootstrap_registered", server_id=server_id, target=target)
        return BootstrapResult(skipped=False, installed=True, target=target)

    async def _install(
        self, host: str, labels: dict[str, Any], credential_id: str | None
    ) -> bool:
        """经 SSH 装 node_exporter。失败返回 False(不抛,不登记目标)。"""
        ssh_target = SSHTarget(
            host=host,
            port=int(labels.get("ssh_port", 22)),
            username=str(labels.get("ssh_username", "root")),
            credential_id=credential_id or "",
        )
        executor = SSHExecutor(ssh_target, self._secrets, connector=self._connector)
        try:
            await NodeExporterInstaller(executor).ensure_installed(port=self._port)
            return True
        except Exception as exc:
            log.warning(
                "monitoring_bootstrap_install_failed",
                host=host,
                error_type=type(exc).__name__,
            )
            return False
