"""Prometheus HTTP 查询客户端(T1.14,设计 §15.4)。

把 PromQL 转发到 Prometheus 的 HTTP API(/api/v1/query 与 /api/v1/query_range),
解析其响应 envelope,屏蔽 Prometheus 直连——上层只拿到 data 或统一的 AppError。

设计要点:
- http client 通过依赖注入(生产传 httpx.AsyncClient,测试传 fake),自身只
  负责 URL/参数拼装与响应解析,不关心连接如何建立。
- Prometheus 响应本身带 {"status": "success"|"error"} envelope:status=error
  (如 PromQL 语法错)映射为 4xx 语义的 AppError;HTTP 层故障(连不上/超时/
  非 200)映射为 502,且不回传底层异常原文(脱敏,§security)。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("prometheus_client")

DEFAULT_TIMEOUT = 10.0


class HttpClientLike(Protocol):
    """httpx.AsyncClient 的最小子集(仅本客户端用到的 get + async 上下文)。"""

    async def get(self, url: str, params: dict[str, Any], timeout: float) -> Any: ...
    async def __aenter__(self) -> HttpClientLike: ...
    async def __aexit__(self, *exc: Any) -> None: ...


class PrometheusClient:
    """转发 PromQL 到 Prometheus HTTP API 并解析结果。"""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: HttpClientLike | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client
        self._timeout = timeout

    async def query(self, promql: str) -> dict[str, Any]:
        """即时查询:转发到 /api/v1/query,返回 data。"""
        return await self._request("/api/v1/query", {"query": promql})

    async def query_range(
        self, promql: str, *, start: float, end: float, step: float
    ) -> dict[str, Any]:
        """区间查询:转发到 /api/v1/query_range,带 start/end/step。"""
        return await self._request(
            "/api/v1/query_range",
            {"query": promql, "start": start, "end": end, "step": step},
        )

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            http = self._http if self._http is not None else self._build_client()
            async with http as conn:
                response = await conn.get(url, params=params, timeout=self._timeout)
        except AppError:
            raise
        except Exception as exc:
            log.warning("prometheus_request_failed", error_type=type(exc).__name__)
            raise AppError(
                "prometheus_unavailable",
                "监控查询后端暂不可用",
                status_code=502,
            ) from exc

        if response.status_code != 200:
            log.warning("prometheus_http_status", status_code=response.status_code)
            raise AppError(
                "prometheus_unavailable",
                "监控查询后端暂不可用",
                status_code=502,
            )

        payload = response.json()
        if payload.get("status") != "success":
            # PromQL 语法错等:Prometheus 返回 200 + status=error,属客户端问题
            raise AppError(
                "prometheus_query_error",
                f"PromQL 查询错误: {payload.get('error', '未知错误')}",
                status_code=400,
            )
        return payload.get("data", {})

    def _build_client(self) -> HttpClientLike:
        import httpx

        return httpx.AsyncClient()
