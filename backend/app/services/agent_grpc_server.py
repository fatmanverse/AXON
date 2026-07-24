"""Agent gRPC server 启动器(T4.1 wire,设计 §15.5)。

把 AgentServicer 挂到一个 grpc.aio server 上并监听。控制面进程(FastAPI lifespan)
按 settings.agent_grpc_enabled 决定是否起 server:关闭时纯 SSH 部署(默认),开启后
Agent 可主动外连建双向流。

设计要点:
- **与连接管理器共享单例**:server 持有的 AgentServicer 用同一个 AgentConnectionManager,
  这样 AgentGateway(经 manager 下发命令)与 gRPC 流(经 manager 收 ACK)是同一份连接注册,
  命令下发与结果回传才能对上(§5.1「统一模型对上,多态执行对下」)。
- **优雅停机**:stop 给正在执行的 RPC 一个宽限期再强杀,避免切断执行中的命令流。
- **测试可注入端口 0**:让 OS 分配空闲端口,拿回实际端口做集成验证,不硬编端口。
"""

from __future__ import annotations

from pathlib import Path

import grpc

from app.core.logging import get_logger
from app.grpc_gen import agent_pb2_grpc
from app.services.agent_connection import AgentConnectionManager
from app.services.agent_grpc import AgentServicer

log = get_logger("agent_grpc_server")


class AgentGrpcServer:
    """封装 grpc.aio server 的生命周期,绑定共享的 AgentConnectionManager。"""

    def __init__(
        self,
        manager: AgentConnectionManager,
        *,
        host: str = "0.0.0.0",  # noqa: S104 - Agent 从各内网机器外连,须监听全网卡
        port: int = 50051,
        grace_period: float = 5.0,
        tls_enabled: bool = False,
        server_cert_file: str = "",
        server_key_file: str = "",
        client_ca_file: str = "",
        revoked_agent_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._manager = manager
        self._host = host
        self._port = port
        self._grace = grace_period
        self._tls_enabled = tls_enabled
        self._server_cert_file = server_cert_file
        self._server_key_file = server_key_file
        self._client_ca_file = client_ca_file
        self._revoked_agent_ids = revoked_agent_ids
        self._server: grpc.aio.Server | None = None
        self._bound_port: int | None = None

    @property
    def bound_port(self) -> int | None:
        """实际监听端口(端口传 0 时由 OS 分配,启动后回填)。"""
        return self._bound_port

    async def start(self) -> None:
        """建 server、注册 servicer、绑定端口并开始服务。幂等:已启动则忽略。"""
        if self._server is not None:
            return
        server = grpc.aio.server()
        agent_pb2_grpc.add_AgentServiceServicer_to_server(
            AgentServicer(
                self._manager,
                require_client_identity=self._tls_enabled,
                revoked_agent_ids=self._revoked_agent_ids,
            ),
            server,
        )
        address = f"{self._host}:{self._port}"
        if self._tls_enabled:
            if not all((self._server_cert_file, self._server_key_file, self._client_ca_file)):
                raise ValueError(
                    "server_cert_file, server_key_file and client_ca_file are required for mTLS"
                )
            cert = Path(self._server_cert_file).read_bytes()
            key = Path(self._server_key_file).read_bytes()
            client_ca = Path(self._client_ca_file).read_bytes()
            credentials = grpc.ssl_server_credentials(
                ((key, cert),),
                root_certificates=client_ca,
                require_client_auth=True,
            )
            self._bound_port = server.add_secure_port(address, credentials)
        else:
            self._bound_port = server.add_insecure_port(address)
        await server.start()
        self._server = server
        log.info("agent_grpc_started", host=self._host, port=self._bound_port)

    async def stop(self) -> None:
        """优雅停机:宽限期内让在途 RPC 收尾,再关闭。幂等。"""
        if self._server is None:
            return
        await self._server.stop(self._grace)
        log.info("agent_grpc_stopped")
        self._server = None
