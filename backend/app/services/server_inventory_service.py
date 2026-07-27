"""服务器现状读取编排(块一:读取"这台机器上到底跑着什么")。

职责:按 server_id 载 Server → 用共享工厂 build_executor_for_server 组装 Executor →
经 InventoryProbe 并发探测容器/服务/端口/资源,汇总成 ServerInventory。

- 仅 SSH 接入的服务器支持实时探测:Agent 模式以心跳/上报为准,这里明确拒绝(400),
  不静默返回空——避免"看起来探测过其实没探"的误导。
- 探测项内部各自降级(见 InventoryProbe);本层只负责选对连接方式并把结果透传。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.server_inventory import InventoryProbe, ServerInventory
from app.adapters.ssh_executor import SSHExecutor
from app.core.db import Database
from app.core.errors import AppError
from app.core.secrets import SecretStore
from app.models.server import AccessMode
from app.services.executor_factory import build_executor_for_server
from app.services.server_repository import ServerRepository


class ServerInventoryService:
    """读取单台服务器的运行时现状快照。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._connector = connector

    async def read(self, server_id: str) -> ServerInventory:
        """探测服务器现状。服务器不存在抛 404;非 SSH 接入抛 400。"""
        async with self._db.session() as session:
            server = await ServerRepository(session).get(server_id)  # 404 if missing
            access_mode = server.access_mode
            executor = build_executor_for_server(server, self._secrets, connector=self._connector)

        if access_mode != AccessMode.SSH or not isinstance(executor, SSHExecutor):
            raise AppError(
                "inventory_requires_ssh",
                "仅 SSH 接入的服务器支持现状探测；Agent 模式以心跳上报为准",
                status_code=400,
            )

        return await InventoryProbe(executor).probe()
