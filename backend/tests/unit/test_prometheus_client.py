"""T1.14 Prometheus 查询客户端(adapter,设计 §15.4)。

用 fake http client 验证:
- query 转发 PromQL 到 /api/v1/query,解析 Prometheus 成功响应的 data。
- query_range 转发到 /api/v1/query_range,带 start/end/step。
- Prometheus 返回 status!=success(如语法错误)抛 AppError。
- HTTP 层异常(连不上/超时)抛 AppError,不泄漏内部细节。

单测不触碰真实 Prometheus。
"""

import pytest

from app.adapters.prometheus_client import PrometheusClient
from app.core.errors import AppError


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    """记录请求;按预置返回 _FakeResponse 或抛错。"""

    def __init__(self, *, response=None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def get(self, url: str, params: dict, timeout: float):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self._raise is not None:
            raise self._raise
        return self._response

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


_OK_VECTOR = {
    "status": "success",
    "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1720000000, "0.5"]}]},
}


async def test_query_forwards_promql_and_returns_data():
    http = _FakeHttpClient(response=_FakeResponse(200, _OK_VECTOR))
    client = PrometheusClient("http://prom:9090", http_client=http, timeout=7.0)

    data = await client.query("up")

    assert data["resultType"] == "vector"
    call = http.calls[0]
    assert call["url"] == "http://prom:9090/api/v1/query"
    assert call["params"]["query"] == "up"
    assert call["timeout"] == 7.0


async def test_query_range_includes_time_bounds_and_step():
    http = _FakeHttpClient(response=_FakeResponse(200, _OK_VECTOR))
    client = PrometheusClient("http://prom:9090", http_client=http)

    await client.query_range("node_cpu_seconds_total", start=100.0, end=200.0, step=15.0)

    call = http.calls[0]
    assert call["url"] == "http://prom:9090/api/v1/query_range"
    assert call["params"]["query"] == "node_cpu_seconds_total"
    assert call["params"]["start"] == 100.0
    assert call["params"]["end"] == 200.0
    assert call["params"]["step"] == 15.0


async def test_base_url_trailing_slash_is_normalized():
    http = _FakeHttpClient(response=_FakeResponse(200, _OK_VECTOR))
    client = PrometheusClient("http://prom:9090/", http_client=http)

    await client.query("up")

    assert http.calls[0]["url"] == "http://prom:9090/api/v1/query"


async def test_prometheus_error_status_raises_app_error():
    err = {"status": "error", "errorType": "bad_data", "error": "parse error"}
    http = _FakeHttpClient(response=_FakeResponse(200, err))
    client = PrometheusClient("http://prom:9090", http_client=http)

    with pytest.raises(AppError) as excinfo:
        await client.query("up{")

    assert excinfo.value.code == "prometheus_query_error"


async def test_non_200_status_raises_app_error():
    http = _FakeHttpClient(response=_FakeResponse(503, {}))
    client = PrometheusClient("http://prom:9090", http_client=http)

    with pytest.raises(AppError) as excinfo:
        await client.query("up")

    assert excinfo.value.code == "prometheus_unavailable"


async def test_http_exception_raises_app_error_without_leaking():
    http = _FakeHttpClient(raise_exc=OSError("connection refused"))
    client = PrometheusClient("http://prom:9090", http_client=http)

    with pytest.raises(AppError) as excinfo:
        await client.query("up")

    assert excinfo.value.code == "prometheus_unavailable"
    # 对外消息不含底层异常原文(脱敏)
    assert "connection refused" not in excinfo.value.message
