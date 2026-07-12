"""SSHExecutor 实现(T1.4,设计 §5.1)。

基于 AsyncSSH 的点对点执行器:私钥从凭证保险箱按 credential_id 取,
执行命令捕获 stdout/stderr/exit,支持超时。连接层通过 connector 注入,
便于单测隔离真实网络。

安全:私钥不落业务表、不常驻 executor 属性,每次建连时从保险箱取用后
即随连接生命周期释放(§13 凭证保险箱)。
"""

from __future__ import annotations

import asyncio
import base64
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore

log = get_logger("ssh_executor")

# 连接工厂:返回一个支持 async with + run() 的连接对象。
# 默认用 asyncssh.connect;测试注入 fake。
Connector = Callable[..., Any]

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class SSHTarget:
    """SSH 连接目标。私钥不在此,靠 credential_id 引用保险箱。"""

    host: str
    port: int
    username: str
    credential_id: str
    connect_timeout: float = 10.0


def _default_connector(**kwargs: Any) -> Any:
    import asyncssh

    return asyncssh.connect(**kwargs)


class SSHExecutor(Executor):
    """经 SSH 在单台服务器上执行动作。"""

    def __init__(
        self,
        target: SSHTarget,
        secret_store: SecretStore,
        *,
        connector: Connector | None = None,
    ) -> None:
        self._target = target
        self._secrets = secret_store
        self._connector = connector or _default_connector

    def _connect(self) -> Any:
        # 每次建连时从保险箱取私钥,不缓存明文
        client_key = self._secrets.get(self._target.credential_id)
        return self._connector(
            host=self._target.host,
            port=self._target.port,
            username=self._target.username,
            client_key=client_key,
            connect_timeout=self._target.connect_timeout,
            known_hosts=None,
        )

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        try:
            async with self._connect() as conn:
                process = await asyncio.wait_for(
                    conn.run(command, timeout=effective_timeout),
                    timeout=effective_timeout,
                )
        except TimeoutError as exc:
            log.warning("ssh_exec_timeout", host=self._target.host, command=command)
            raise AppError(
                "ssh_timeout",
                f"SSH 命令执行超时({effective_timeout}s)",
                status_code=504,
            ) from exc
        except AppError:
            raise
        except Exception as exc:
            log.warning(
                "ssh_exec_failed",
                host=self._target.host,
                error_type=type(exc).__name__,
            )
            raise AppError(
                "ssh_error",
                f"SSH 命令执行失败: {exc}",
                status_code=502,
            ) from exc

        return CommandResult(
            exit_code=process.exit_status or 0,
            stdout=process.stdout or "",
            stderr=process.stderr or "",
        )

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        # 裸机部署的 MVP 形态:拉取/切换制品的命令由 runtime 适配层细化(T1.7/1.8)。
        # 此处提供通用落点——把制品地址与环境变量组装成一条可执行命令。
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in spec.env.items())
        command = f"{env_prefix} deploy {shlex.quote(spec.artifact)}".strip()
        return await self.exec(command)

    async def update_config(self, path: str, content: str) -> CommandResult:
        # 用 base64 编码传输内容:heredoc 会因内容含分隔符行而提前终止(残余内容被当
        # 命令执行,命令注入/文件损坏),base64 载荷只含 [A-Za-z0-9+/=],无 shell 可解释
        # 字符,与内容无关地安全。path 经 shlex.quote 防注入;reload/restart 由上层决定。
        encoded = base64.b64encode(content.encode()).decode()
        command = f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}"
        return await self.exec(command)

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        result = await self.exec(f"systemctl is-active {shlex.quote(service_ref)}")
        running = result.stdout.strip() == "active"
        return ServiceStatus(
            name=service_ref,
            running=running,
            detail=result.stdout.strip() or result.stderr.strip(),
        )

    async def test_connectivity(self) -> bool:
        """连通性测试:能建连并跑一条无害命令即视为通。失败返回 False,不抛。"""
        try:
            result = await self.exec("true", timeout=self._target.connect_timeout)
            return result.succeeded
        except Exception:
            log.info("ssh_connectivity_failed", host=self._target.host)
            return False
