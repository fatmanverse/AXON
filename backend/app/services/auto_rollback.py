"""告警触发自动回滚的决策逻辑(T3.8,§11.2)。

纯决策函数与传输/DB 解耦,便于单测。安全优先:全局开关默认关闭,只有
critical + firing + 可关联 service 的告警在开关开启时才触发。resolved、
非 critical、无 service 一律不触发,避免误回滚改动生产状态。
"""

from __future__ import annotations

from app.models.alert import AlertSeverity, AlertStatus


def should_auto_rollback(
    *,
    severity: AlertSeverity,
    status: AlertStatus,
    service: str | None,
    enabled: bool,
) -> bool:
    """判断一条告警是否应触发该 service 的自动回滚。"""
    if not enabled:
        return False
    if service is None:
        return False
    if severity != AlertSeverity.CRITICAL:
        return False
    if status != AlertStatus.FIRING:
        return False
    return True
