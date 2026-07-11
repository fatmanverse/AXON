"""T3.8 告警触发自动回滚的决策逻辑(§11.2)。

should_auto_rollback 是纯函数,判断一条告警是否应触发自动回滚:
- 全局开关关闭 → 恒 False(默认关闭,改变生产状态须显式开启)。
- 仅 severity=critical + status=firing + service 非空 且 开关开启 → True。
- warning/info、resolved、无 service 均不触发。
"""

from app.models.alert import AlertSeverity, AlertStatus
from app.services.auto_rollback import should_auto_rollback


def test_critical_firing_with_service_and_flag_on_triggers():
    assert should_auto_rollback(
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.FIRING,
        service="billing",
        enabled=True,
    ) is True


def test_flag_off_never_triggers():
    assert should_auto_rollback(
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.FIRING,
        service="billing",
        enabled=False,
    ) is False


def test_non_critical_does_not_trigger():
    for sev in (AlertSeverity.WARNING, AlertSeverity.INFO):
        assert should_auto_rollback(
            severity=sev, status=AlertStatus.FIRING, service="billing", enabled=True
        ) is False


def test_resolved_does_not_trigger():
    assert should_auto_rollback(
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.RESOLVED,
        service="billing",
        enabled=True,
    ) is False


def test_no_service_does_not_trigger():
    assert should_auto_rollback(
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.FIRING,
        service=None,
        enabled=True,
    ) is False
