"""AgentGateway:经 Agent 通道执行动作(T4.3,设计 §5.1/§5.3/§5.4)。

统一 Executor 接口的 Agent 实现。两种构造形态,上层零改动地平滑升级:

- **占位形态**(无参构造,MVP 默认):未接入 Agent 连接管理器时,所有动作抛
  AgentNotConnectedError(501),保证 access_mode=agent 的操作返回明确的"未接入"
  提示而非 500/静默失败,且不影响 SSH 路径(§5.3)。

- **真实形态**(注入 manager + agent_id):经 AgentConnectionManager 下发
  ServerCommand(§15.5),await 对应 task_id 的 result ACK(§5.4① 两段 ACK 的第二段
  才推进结果)。ack_timeout 内无 result 抛 504——上层据此判 task 为 unknown
  而非武断 failed(§5.4④)。命令携带 fence token(§5.4⑥ 幂等基石)。

从 SSH 平滑升级到 Agent 时,业务与 UI 一行不改(§5.1「统一模型对上,多态执行对下」)。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger
from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    AgentRoutingError,
    ServerCommand,
)

log = get_logger("agent_gateway")


class AgentNotConnectedError(AppError):
    """Agent 通道尚未接入(占位形态)或目标 agent 无活跃连接。

    用 501 Not Implemented:语义上是"此能力暂未接入",区别于客户端错误(4xx)
    与服务端故障(500),便于前端针对性提示。
    """

    _DEFAULT = "该服务器为 Agent 接入模式,Agent 通道尚未接入,暂不支持此操作"

    def __init__(self, message: str | None = None) -> None:
        super().__init__("agent_not_connected", message or self._DEFAULT, status_code=501)


class AgentGateway(Executor):
    """Agent 执行器。无 manager 时为占位(拒绝所有动作);注入后经连接管理器真实下发。"""

    def __init__(
        self,
        *,
        manager: AgentConnectionManager | None = None,
        agent_id: str | None = None,
        ack_timeout: float = 30.0,
        fence: int = 0,
        artifact_chunk_bytes: int = 192 * 1024,
        artifact_max_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self._manager = manager
        self._agent_id = agent_id
        self._ack_timeout = ack_timeout
        self._fence = fence
        if artifact_chunk_bytes <= 0 or artifact_max_bytes <= 0:
            raise ValueError("Agent 制品分块和大小上限必须为正数")
        self._artifact_chunk_bytes = artifact_chunk_bytes
        self._artifact_max_bytes = artifact_max_bytes
        # task_id → 等待 result ACK 的 future。收到 ACK 时 resolve。
        self._pending: dict[str, asyncio.Future[AgentMessage]] = {}
        if manager is not None:
            manager.on_message(self._on_message)

    def _on_message(self, message: AgentMessage) -> None:
        """连接管理器分发来的 Agent 上报:result ACK 唤醒对应 task 的等待者。"""
        if message.kind != AgentMessageKind.RESULT or message.task_id is None:
            return
        future = self._pending.get(message.task_id)
        if future is not None and not future.done():
            future.set_result(message)

    async def _dispatch(self, action: str, params: dict[str, str]) -> CommandResult:
        """下发一条命令并等 result ACK。占位形态直接拒绝;离线抛错;超时抛 504。"""
        if self._manager is None or self._agent_id is None:
            raise AgentNotConnectedError()

        task_id = uuid_hex()
        command = ServerCommand(task_id=task_id, action=action, params=params, fence=self._fence)
        future: asyncio.Future[AgentMessage] = asyncio.get_running_loop().create_future()
        self._pending[task_id] = future
        try:
            try:
                await self._manager.send_command(self._agent_id, command)
            except KeyError as exc:
                # 无活跃连接:503 服务暂不可用(区别于 501「通道未接入」的占位形态)。
                # 上层据此走离线分档(§5.4⑤ prod 高危拒绝/低危 TTL 排队)。
                raise AppError(
                    "agent_offline",
                    f"Agent 无活跃连接,无法下发: {self._agent_id}",
                    status_code=503,
                ) from exc
            except AgentRoutingError as exc:
                raise AppError(
                    "agent_routing_unavailable",
                    "Agent 跨实例路由不可用,请稍后重试",
                    status_code=503,
                ) from exc

            try:
                ack = await asyncio.wait_for(future, timeout=self._ack_timeout)
            except TimeoutError as exc:
                # 超时可能已执行:抛 504,上层据此判 task 为 unknown(§5.4④)
                log.warning("agent_ack_timeout", agent_id=self._agent_id, task_id=task_id)
                raise AppError(
                    "agent_ack_timeout",
                    f"Agent 命令超时未回结果({self._ack_timeout}s),状态待核对",
                    status_code=504,
                ) from exc
        finally:
            self._pending.pop(task_id, None)

        if ack.ok:
            return CommandResult(exit_code=0, stdout=ack.detail, stderr="")
        return CommandResult(exit_code=1, stdout="", stderr=ack.detail)

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        return await self._dispatch("exec", {"command": command})

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        params = {"artifact": spec.artifact}
        for key, value in spec.env.items():
            params[f"env.{key}"] = value
        return await self._dispatch("deploy", params)

    async def update_config(self, path: str, content: str) -> CommandResult:
        return await self._dispatch("update_config", {"path": path, "content": content})

    async def upload_artifact(self, local_path: str, remote_path: str) -> None:
        """以 bounded chunks 上传制品，Agent commit 时再做长度和 SHA-256 校验。"""
        path = Path(local_path)
        if not path.is_file():
            raise AppError(
                "artifact_local_not_found",
                f"本地制品文件不存在: {local_path}",
                status_code=404,
            )
        size = path.stat().st_size
        if size > self._artifact_max_bytes:
            raise AppError(
                "artifact_too_large",
                f"制品超过 Agent 制品上限({self._artifact_max_bytes} bytes)",
                status_code=413,
            )

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(self._artifact_chunk_bytes):
                digest.update(chunk)
        transfer_id = uuid_hex()
        await self._dispatch_checked(
            "artifact_begin",
            {
                "transfer_id": transfer_id,
                "remote_path": remote_path,
                "size": str(size),
                "sha256": digest.hexdigest(),
            },
        )
        offset = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(self._artifact_chunk_bytes):
                    await self._dispatch_checked(
                        "artifact_chunk",
                        {
                            "transfer_id": transfer_id,
                            "offset": str(offset),
                            "data": base64.b64encode(chunk).decode("ascii"),
                            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                        },
                    )
                    offset += len(chunk)
            await self._dispatch_checked("artifact_commit", {"transfer_id": transfer_id})
        except Exception:
            try:
                await self._dispatch_checked("artifact_abort", {"transfer_id": transfer_id})
            except Exception as cleanup_exc:  # noqa: BLE001 - 保留原始失败结论
                log.warning(
                    "agent_artifact_abort_failed",
                    transfer_id=transfer_id,
                    error_type=type(cleanup_exc).__name__,
                )
            raise

    async def _dispatch_checked(self, action: str, params: dict[str, str]) -> None:
        result = await self._dispatch(action, params)
        if not result.succeeded:
            raise AppError(
                "agent_artifact_upload_failed",
                f"Agent 制品动作失败:{action}: {result.stderr or result.stdout}",
                status_code=502,
            )

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        result = await self._dispatch("status", {"service_ref": service_ref})
        running = result.succeeded and result.stdout.strip() in ("active", "running")
        return ServiceStatus(
            name=service_ref,
            running=running,
            detail=result.stdout.strip() or result.stderr.strip(),
        )


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex
