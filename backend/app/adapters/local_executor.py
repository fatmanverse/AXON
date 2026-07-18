"""LocalExecutor 本地子进程执行器(构建能力一期,§方案 A「1 号构建节点」)。

控制面主机自身执行构建命令(git clone → 测试 → build)。与 SSHExecutor 同一
Executor 接缝:上层(BuildRunner/BuildService)只依赖 exec 语义,后续把构建
派到 SSH 构建节点时上层一行不改。

语义对齐 SSHExecutor:
- 命令业务失败(非 0)靠 CommandResult 表达,不抛;
- 基础设施故障才抛 AppError——超时 local_exec_timeout(504,对齐 ssh_timeout)。
- deploy/update_config/get_service_status 非构建执行职责,本实现不承担
  (本地节点是构建接缝,不是运行时部署目标),抛 NotImplementedError。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("local_executor")

# 构建步骤(clone/测试/build)普遍远慢于运维命令,默认超时给足半小时。
DEFAULT_TIMEOUT = 1800.0


class LocalExecutor(Executor):
    """在控制面主机上以子进程执行命令(限定 workdir)。"""

    def __init__(self, workdir: Path | str) -> None:
        self._workdir = Path(workdir)

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        effective_timeout = timeout or DEFAULT_TIMEOUT
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=effective_timeout
            )
        except TimeoutError:
            # 超时先杀进程再抛,避免遗留孤儿构建进程占资源。
            process.kill()
            await process.wait()
            log.warning("local_exec_timeout", timeout=effective_timeout)
            raise AppError(
                "local_exec_timeout",
                f"本地命令执行超时({effective_timeout}s)",
                status_code=504,
            ) from None
        return CommandResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    async def deploy(self, spec: DeploySpec) -> CommandResult:
        raise NotImplementedError("LocalExecutor 仅承担构建执行,不做运行时部署")

    async def update_config(self, path: str, content: str) -> CommandResult:
        raise NotImplementedError("LocalExecutor 仅承担构建执行,不做配置下发")

    async def get_service_status(self, service_ref: str) -> ServiceStatus:
        raise NotImplementedError("LocalExecutor 仅承担构建执行,不做服务状态观测")
