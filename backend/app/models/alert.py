"""alerts 告警模型(§14.8)。

Alertmanager 告警规则触发后经 webhook 回调控制面(§6.3)。每条告警按
Alertmanager 提供的 fingerprint 幂等去重:同一 fingerprint 重复上报(firing→
resolved 状态流转)收敛为幂等更新(仓储层保证)。service 尽力关联(告警标签里
带 service 时挂上,否则留空)。
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


class AlertSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


def _uuid() -> str:
    return uuid.uuid4().hex


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    # 幂等键:Alertmanager 每条告警带稳定 fingerprint,重复上报幂等更新同一行。
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_alerts_fingerprint"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # fingerprint 是 Alertmanager 侧稳定标识,作幂等键
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # service 尽力关联(告警标签含 service 时挂上),可空
    service: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, name="alert_severity", values_callable=_enum_values),
        nullable=False,
        default=AlertSeverity.WARNING,
    )
    summary: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="alertmanager")
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status", values_callable=_enum_values),
        nullable=False,
        default=AlertStatus.FIRING,
        index=True,
    )
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
