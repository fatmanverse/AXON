"""T1.14 指标查询服务(PromQL 白名单 + 转发,设计 §15.4)。

MetricsService 在把查询转发给 Prometheus 前做安全校验:
- 只放行白名单内的指标名(前缀匹配),拦截任意 PromQL 注入探测。
- 空查询、超长查询被拒。
- 通过校验的查询转发给注入的 client,原样返回其 data。

用 fake client,不触真实 Prometheus。
"""

import pytest

from app.core.errors import AppError
from app.services.metrics_service import MetricsService

_ALLOWED = ("up", "node_cpu_seconds_total", "node_memory_")


class _FakeClient:
    def __init__(self) -> None:
        self.queried: list[str] = []

    async def query(self, promql: str):
        self.queried.append(promql)
        return {"resultType": "vector", "result": []}

    async def query_range(self, promql: str, *, start, end, step):
        self.queried.append(promql)
        return {"resultType": "matrix", "result": []}


def _service(client, allowed=_ALLOWED, max_len=1000) -> MetricsService:
    return MetricsService(client, allowed_metrics=allowed, max_query_len=max_len)


async def test_allowed_metric_is_forwarded():
    client = _FakeClient()
    data = await _service(client).query("up")

    assert data["resultType"] == "vector"
    assert client.queried == ["up"]


async def test_allowed_metric_with_label_selector_is_forwarded():
    client = _FakeClient()
    await _service(client).query('node_cpu_seconds_total{mode="idle"}')

    assert client.queried == ['node_cpu_seconds_total{mode="idle"}']


async def test_prefix_allowlist_matches_family():
    client = _FakeClient()
    await _service(client).query("node_memory_MemAvailable_bytes")

    assert len(client.queried) == 1


async def test_metric_not_in_allowlist_is_rejected():
    client = _FakeClient()
    with pytest.raises(AppError) as excinfo:
        await _service(client).query("secret_internal_metric")

    assert excinfo.value.code == "metric_not_allowed"
    assert client.queried == []


async def test_empty_query_rejected():
    client = _FakeClient()
    with pytest.raises(AppError) as excinfo:
        await _service(client).query("   ")

    assert excinfo.value.code == "invalid_query"
    assert client.queried == []


async def test_overlong_query_rejected():
    client = _FakeClient()
    long_q = "up" + " " * 2000
    with pytest.raises(AppError) as excinfo:
        await _service(client, max_len=100).query(long_q)

    assert excinfo.value.code == "invalid_query"
    assert client.queried == []


async def test_query_range_also_validated():
    client = _FakeClient()
    with pytest.raises(AppError) as excinfo:
        await _service(client).query_range(
            "rm_rf_metric", start=1.0, end=2.0, step=15.0
        )

    assert excinfo.value.code == "metric_not_allowed"


async def test_query_range_allowed_forwards():
    client = _FakeClient()
    data = await _service(client).query_range(
        "node_cpu_seconds_total", start=1.0, end=2.0, step=15.0
    )

    assert data["resultType"] == "matrix"
    assert client.queried == ["node_cpu_seconds_total"]
