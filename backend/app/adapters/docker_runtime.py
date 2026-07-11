"""docker 运行时适配(T1.8,设计 §5.1)。

把服务生命周期动作(start/stop/restart/delete/status)翻译成 docker 命令,
经注入的统一 Executor 执行,并解析 docker 输出。runtime_ref 形如
``{"container_name": "billing"}``,container_name 即此处的动作目标。

设计取舍(与 systemd 适配同构,见 systemd_runtime):
- docker 的「删除」映射为 rm -f:强制移除容器(即使在运行),这是运维视角最
  接近「下线一个 docker 服务」的动作,与 systemd 的 disable --now 语义对齐。
- start/stop/restart/delete 属变更类动作,docker 返回非 0 即视为失败,抛
  AppError 携带 stderr 供上层定位;而 status 走 inspect,对不存在的容器本就
  返回非 0,故只反映 running=False,绝不抛错。
- status 用 ``docker inspect --format '{{.State.Running}}'`` 直接取布尔状态,
  比解析 ``docker ps`` 表格更健壮,输出稳定为 true/false。
- 所有 container_name 一律经 shlex.quote 转义,杜绝命令注入。
"""

from __future__ import annotations

import shlex

from app.adapters.executor import Executor, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("docker_runtime")

# docker inspect 判定「运行中」的输出。其余(false/空/报错)均非运行。
_RUNNING_STATE = "true"


class DockerRuntime:
    """docker 生命周期动作适配器。

    依赖注入一个 Executor(生产传 SSHExecutor,测试传 fake),自身只负责把
    动作翻译成 docker 命令并解析结果,不关心命令如何送达目标主机。
    """

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def start(self, container_name: str) -> None:
        """启动容器:docker start <name>。失败抛 AppError。"""
        await self._run_lifecycle(
            "start", container_name, f"docker start {shlex.quote(container_name)}"
        )

    async def stop(self, container_name: str) -> None:
        """停止容器:docker stop <name>。失败抛 AppError。"""
        await self._run_lifecycle(
            "stop", container_name, f"docker stop {shlex.quote(container_name)}"
        )

    async def restart(self, container_name: str) -> None:
        """重启容器:docker restart <name>。失败抛 AppError。"""
        await self._run_lifecycle(
            "restart", container_name, f"docker restart {shlex.quote(container_name)}"
        )

    async def delete(self, container_name: str) -> None:
        """下线容器:docker rm -f 强制移除(见模块 docstring 的语义取舍)。"""
        await self._run_lifecycle(
            "delete", container_name, f"docker rm -f {shlex.quote(container_name)}"
        )

    async def status(self, container_name: str) -> ServiceStatus:
        """查询容器状态:docker inspect --format '{{.State.Running}}' <name>。

        inspect 对不存在的容器返回非 0 属正常语义,故此处不抛错,只依据 stdout
        判定 running(仅 "true" 为 True),并把状态原文放进 detail 供上层展示。
        """
        result = await self._executor.exec(
            f"docker inspect --format '{{{{.State.Running}}}}' {shlex.quote(container_name)}"
        )
        state = result.stdout.strip()
        return ServiceStatus(
            name=container_name,
            running=state == _RUNNING_STATE,
            detail=state or result.stderr.strip(),
        )

    async def _run_lifecycle(self, action: str, container_name: str, command: str) -> None:
        """执行一条变更类 docker 命令,非 0 退出即抛 AppError。"""
        result = await self._executor.exec(command)
        if not result.succeeded:
            # 只在服务端日志留容器名与退出码,message 透出 stderr 供上层定位根因
            log.warning(
                "docker_action_failed",
                action=action,
                container=container_name,
                exit_code=result.exit_code,
            )
            raise AppError(
                "docker_action_failed",
                f"docker {action} 失败({container_name}): {result.stderr.strip()}",
                status_code=502,
            )
