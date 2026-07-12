"""发布后健康检查的探测执行端(T3.8,§11.1)。

HealthChecker 只做编排(重试/间隔/判定),真正「探一次」由 Prober 执行。此前只有
测试 fake,生产无真实探测端 → 即便注入 checker 也探不了。本模块补生产实现:
- http:  GET url,状态码命中 expect_status(默认 200)即通过。
- command: 经注入 executor 跑命令,exit 命中 expect_exit(默认 0)即通过。

依赖注入(http_factory / executor_factory)便于测试;生产 http 用 httpx。
command 探测复用统一 Executor(SSH/Agent),即在目标机上跑就绪命令。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.executor import Executor
from app.adapters.pipeline import HttpClientLike
from app.core.logging import get_logger

log = get_logger("health_prober")

DEFAULT_TIMEOUT = 5.0


def _build_httpx() -> HttpClientLike:
    import httpx

    return httpx.AsyncClient()


class DefaultHealthProber:
    """生产健康探测:http 状态码探测 + command 退出码探测。

    http_factory 返回一个 HttpClientLike(默认 httpx.AsyncClient);executor_factory
    返回统一 Executor(command 探测在目标机执行)。command 探测未注入 executor 时报错。
    """

    def __init__(
        self,
        *,
        http_factory: Callable[[], HttpClientLike] = _build_httpx,
        executor_factory: Callable[[], Executor] | None = None,
    ) -> None:
        self._http_factory = http_factory
        self._executor_factory = executor_factory

    async def probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        """执行一次探测。返回 (是否通过, 明细)。未知类型安全失败(不通过)。"""
        probe_type = config.get("type", "http")
        if probe_type == "http":
            return await self._probe_http(config)
        if probe_type == "command":
            return await self._probe_command(config)
        return (False, f"不支持的健康检查类型: {probe_type}")

    async def _probe_http(self, config: dict[str, Any]) -> tuple[bool, str]:
        url = config.get("url")
        if not url:
            return (False, "http 健康检查缺少 url")
        expect = int(config.get("expect_status", 200))
        timeout = float(config.get("timeout_sec", DEFAULT_TIMEOUT))
        try:
            http = self._http_factory()
            async with http as conn:
                resp = await conn.request("GET", url, timeout=timeout)
        except Exception as exc:  # 网络层故障 = 探测失败(不抛,交编排重试)
            return (False, f"http 探测异常: {type(exc).__name__}")
        status = resp.status_code
        ok = status == expect
        return (ok, f"HTTP {status}(期望 {expect})")

    async def _probe_command(self, config: dict[str, Any]) -> tuple[bool, str]:
        command = config.get("command")
        if not command:
            return (False, "command 健康检查缺少 command")
        if self._executor_factory is None:
            return (False, "未配置 executor,无法执行命令探测")
        expect = int(config.get("expect_exit", 0))
        try:
            executor = self._executor_factory()
            result = await executor.exec(command)
        except Exception as exc:
            return (False, f"命令探测异常: {type(exc).__name__}")
        ok = result.exit_code == expect
        detail = f"exit {result.exit_code}(期望 {expect})"
        return (ok, detail)
