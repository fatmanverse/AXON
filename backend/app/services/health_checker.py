"""发布后健康检查(T3.9,§11.1)。

部署完成后按 service.health_check 配置探测服务是否就绪。探测的实际执行
(HTTP 请求 / 命令执行)通过注入的 prober 解耦,本模块只做编排:重试、间隔、
判定。retries 次尝试内任一次通过即健康;全失败才不健康。

health_check 配置形状(存 service.health_check JSON):
- HTTP:  {"type": "http", "url": "...", "expect_status": 200, "retries": 3, "interval_sec": 2}
- 命令:  {"type": "command", "command": "systemctl is-active x", "expect_exit": 0, "retries": 3}
无配置视为健康(MVP 宽松,不阻断)。判定失败由调用方决定是否触发自动回滚(§11.2)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


class Prober(Protocol):
    """探测器:执行一次探测,返回 (是否通过, 明细)。HTTP/命令各有实现。"""

    async def probe(self, config: dict[str, Any]) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    attempts: int
    detail: str = ""


class HealthChecker:
    """按 health_check 配置探测,支持重试。探测执行经注入的 prober 解耦。"""

    def __init__(self, *, prober: Prober) -> None:
        self._prober = prober

    async def check(self, config: dict[str, Any] | None) -> HealthResult:
        # 无配置:视为健康,不阻断(MVP 宽松)
        if not config:
            return HealthResult(healthy=True, attempts=0)

        retries = max(1, int(config.get("retries", 1)))
        interval = float(config.get("interval_sec", 0))

        last_detail = ""
        for attempt in range(1, retries + 1):
            ok, detail = await self._prober.probe(config)
            last_detail = detail
            if ok:
                return HealthResult(healthy=True, attempts=attempt, detail=detail)
            if attempt < retries and interval > 0:
                await asyncio.sleep(interval)

        return HealthResult(healthy=False, attempts=retries, detail=last_detail)
