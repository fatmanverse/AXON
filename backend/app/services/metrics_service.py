"""指标查询服务:PromQL 白名单校验 + 转发(T1.14,设计 §15.4)。

控制面屏蔽 Prometheus 直连,所有 PromQL 经此校验后才转发,防止任意查询注入
探测内部指标或以昂贵查询打爆后端。

校验规则(MVP):
- 非空、长度受限(超长拒绝)。
- 查询里出现的指标名必须命中白名单前缀——用前缀匹配以放行同族指标
  (如 node_memory_* 一次配置覆盖 MemAvailable/MemTotal 等)。
- 校验只做「准入」,不解析完整 PromQL 语法;真正的语法错交给 Prometheus 判定
  并由 PrometheusClient 翻译为 prometheus_query_error。

白名单与长度上限从配置注入,便于按环境收紧/放宽,不改调用方。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.errors import AppError

# PromQL 里指标名的词法:字母/下划线/冒号开头,后接字母数字/下划线/冒号。
# 用它从查询中提取被引用的指标名,逐个比对白名单前缀。
_METRIC_TOKEN = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")

# 提取指标名前先剥离的部分:{…} label 选择器块、以及引号字符串(避免 label
# 名/值被误当作指标名)。非贪婪匹配,逐块替换为空格。
_LABEL_BLOCK = re.compile(r"\{[^}]*\}")
_QUOTED = re.compile(r"""(['"`])(?:\\.|(?!\1).)*\1""")

# PromQL 关键字/聚合算子等非指标标识符,提取指标名时跳过,避免被误判为指标。
_PROMQL_KEYWORDS = frozenset(
    {
        "by",
        "without",
        "on",
        "ignoring",
        "group_left",
        "group_right",
        "offset",
        "bool",
        "and",
        "or",
        "unless",
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "count_values",
        "stddev",
        "stdvar",
        "topk",
        "bottomk",
        "quantile",
        "rate",
        "irate",
        "increase",
        "delta",
        "idelta",
        "deriv",
        "predict_linear",
        "histogram_quantile",
        "abs",
        "ceil",
        "floor",
        "round",
        "sqrt",
        "exp",
        "ln",
        "log2",
        "log10",
        "time",
        "inf",
        "nan",
    }
)

DEFAULT_MAX_QUERY_LEN = 2000


class MetricsBackend(Protocol):
    """指标后端(PrometheusClient)的最小接口。"""

    async def query(self, promql: str) -> dict[str, Any]: ...
    async def query_range(
        self, promql: str, *, start: float, end: float, step: float
    ) -> dict[str, Any]: ...


class MetricsService:
    """在转发前对 PromQL 做准入校验的指标查询服务。"""

    def __init__(
        self,
        backend: MetricsBackend,
        *,
        allowed_metrics: Sequence[str],
        max_query_len: int = DEFAULT_MAX_QUERY_LEN,
    ) -> None:
        self._backend = backend
        self._allowed = tuple(allowed_metrics)
        self._max_len = max_query_len

    async def query(self, promql: str) -> dict[str, Any]:
        self._validate(promql)
        return await self._backend.query(promql)

    async def query_range(
        self, promql: str, *, start: float, end: float, step: float
    ) -> dict[str, Any]:
        self._validate(promql)
        return await self._backend.query_range(promql, start=start, end=end, step=step)

    def _validate(self, promql: str) -> None:
        stripped = promql.strip()
        if not stripped or len(promql) > self._max_len:
            raise AppError("invalid_query", "查询为空或超出长度限制", status_code=400)

        metrics = self._extract_metric_names(stripped)
        if not metrics:
            raise AppError("invalid_query", "查询未包含可识别的指标名", status_code=400)

        for metric in metrics:
            if not any(metric.startswith(prefix) for prefix in self._allowed):
                raise AppError(
                    "metric_not_allowed",
                    f"指标不在白名单内: {metric}",
                    status_code=403,
                )

    def _extract_metric_names(self, promql: str) -> list[str]:
        """从 PromQL 中提取被引用的指标名(跳过关键字/算子、label 块与引号串)。

        先剥离 {…} label 选择器与引号字符串——否则 label 名(如 mode)与 label 值
        会被词法规则误当作指标名。剩余部分里,仅取「后面不紧跟 ( 的、非关键字」的
        标识符:函数名(如 rate() 后带括号)与聚合算子被排除,余下即指标名候选。
        """
        sanitized = _LABEL_BLOCK.sub(" ", _QUOTED.sub(" ", promql))
        names: list[str] = []
        for match in _METRIC_TOKEN.finditer(sanitized):
            token = match.group()
            if token in _PROMQL_KEYWORDS:
                continue
            # 紧跟 '(' 的是函数调用名,不是指标
            tail = sanitized[match.end() :].lstrip()
            if tail.startswith("("):
                continue
            names.append(token)
        return names
