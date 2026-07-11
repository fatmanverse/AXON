"""Prometheus 查询代理 API(T1.14,设计 §15.4)。

控制面屏蔽 Prometheus 直连:前端只经此端点取指标。所有 PromQL 经 MetricsService
白名单校验后才转发,防注入探测内部指标或昂贵查询打爆后端(§13)。

- GET /api/metrics/query        即时查询 → 返回 Prometheus data。
- GET /api/metrics/query_range  区间查询(带 start/end/step)。

需认证;白名单与 Prometheus 地址从配置注入。http client 默认走 httpx,测试可
经 app.state.prometheus_http_client 覆写(见 get_prometheus_http_client)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.adapters.prometheus_client import PrometheusClient
from app.api.deps import get_current_user, get_prometheus_http_client, get_settings
from app.core.config import Settings
from app.core.responses import ok
from app.models.user import User
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _build_service(settings: Settings, http_client) -> MetricsService:
    client = PrometheusClient(
        settings.prometheus_base_url,
        http_client=http_client,
        timeout=settings.prometheus_query_timeout_sec,
    )
    return MetricsService(
        client,
        allowed_metrics=settings.metrics_allowed_prefixes,
        max_query_len=settings.metrics_max_query_len,
    )


@router.get("/query")
async def query(
    query: str = Query(..., description="PromQL 即时查询"),
    settings: Settings = Depends(get_settings),
    http_client=Depends(get_prometheus_http_client),
    _: User = Depends(get_current_user),
) -> dict:
    service = _build_service(settings, http_client)
    data = await service.query(query)
    return ok(data)


@router.get("/query_range")
async def query_range(
    query: str = Query(..., description="PromQL 区间查询"),
    start: float = Query(..., description="起始 Unix 时间戳(秒)"),
    end: float = Query(..., description="结束 Unix 时间戳(秒)"),
    step: float = Query(..., gt=0, description="步长(秒)"),
    settings: Settings = Depends(get_settings),
    http_client=Depends(get_prometheus_http_client),
    _: User = Depends(get_current_user),
) -> dict:
    service = _build_service(settings, http_client)
    data = await service.query_range(query, start=start, end=end, step=step)
    return ok(data)
