"""systemd 运行时适配(T1.7,设计 §5.1)。

把服务生命周期动作(start/stop/restart/delete/status)翻译成 systemctl
命令,经注入的统一 Executor 执行,并解析 systemctl 输出。runtime_ref 形如
``{"unit_name": "billing.service"}``,unit_name 即此处的动作目标。

设计取舍:
- systemd 没有「删除服务」的概念,unit 文件的增删属于配置下发范畴。这里把
  delete 语义化为「停用并取消开机自启」(disable --now),即让服务立即停止
  且重启后不再拉起——这是运维视角最接近「下线一个 systemd 服务」的动作。
- start/stop/restart/delete 属于变更类动作,systemctl 返回非 0 即视为失败,
  抛 AppError 携带 stderr 供上层定位;而 status 走 is-active,对未运行服务
  本就返回非 0,故只反映 running=False,绝不抛错。
- 所有 unit_name 一律经 shlex.quote 转义,杜绝命令注入。
"""

from __future__ import annotations

import shlex

from app.adapters.executor import Executor, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("systemd_runtime")

# systemctl 判定「运行中」的唯一激活态。其余(inactive/failed/activating…)均非运行。
_ACTIVE_STATE = "active"


class SystemdRuntime:
    """systemd 生命周期动作适配器。

    依赖注入一个 Executor(生产传 SSHExecutor,测试传 fake),自身只负责把
    动作翻译成 systemctl 命令并解析结果,不关心命令如何送达目标主机。
    """

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def start(self, unit_name: str) -> None:
        """启动服务:systemctl start <unit>。失败抛 AppError。"""
        await self._run_lifecycle("start", unit_name, f"systemctl start {shlex.quote(unit_name)}")

    async def stop(self, unit_name: str) -> None:
        """停止服务:systemctl stop <unit>。失败抛 AppError。"""
        await self._run_lifecycle("stop", unit_name, f"systemctl stop {shlex.quote(unit_name)}")

    async def restart(self, unit_name: str) -> None:
        """重启服务:systemctl restart <unit>。失败抛 AppError。"""
        await self._run_lifecycle(
            "restart", unit_name, f"systemctl restart {shlex.quote(unit_name)}"
        )

    async def delete(self, unit_name: str) -> None:
        """下线服务:disable --now 停用并取消开机自启(见模块 docstring 的语义取舍)。"""
        await self._run_lifecycle(
            "delete", unit_name, f"systemctl disable --now {shlex.quote(unit_name)}"
        )

    async def status(self, unit_name: str) -> ServiceStatus:
        """查询服务状态:systemctl is-active <unit>。

        is-active 对未运行服务返回非 0 属正常语义,故此处不抛错,只依据 stdout
        判定 running(仅 "active" 为 True),并把状态原文放进 detail 供上层展示。
        """
        result = await self._executor.exec(f"systemctl is-active {shlex.quote(unit_name)}")
        state = result.stdout.strip()
        return ServiceStatus(
            name=unit_name,
            running=state == _ACTIVE_STATE,
            detail=state or result.stderr.strip(),
        )

    async def _run_lifecycle(self, action: str, unit_name: str, command: str) -> None:
        """执行一条变更类 systemctl 命令,非 0 退出即抛 AppError。"""
        result = await self._executor.exec(command)
        if not result.succeeded:
            # 只在服务端日志留 unit 与退出码,message 透出 stderr 供上层定位根因
            log.warning(
                "systemd_action_failed",
                action=action,
                unit=unit_name,
                exit_code=result.exit_code,
            )
            raise AppError(
                "systemd_action_failed",
                f"systemd {action} 失败({unit_name}): {result.stderr.strip()}",
                status_code=502,
            )
