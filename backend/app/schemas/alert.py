"""alerts 的边界 schema(§6.3/§14.8)。

对齐 Alertmanager webhook 的批量 payload:顶层含 alerts 数组,每条 alert 带
fingerprint、status、labels(severity/service...)、annotations(summary)、
startsAt/endsAt。控制面按 fingerprint 幂等落库。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.alert import AlertSeverity, AlertStatus


class AlertmanagerAlert(BaseModel):
    """Alertmanager 单条告警(取控制面需要的字段,其余忽略)。"""

    fingerprint: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime | None = None
    endsAt: datetime | None = None


class AlertmanagerWebhookPayload(BaseModel):
    """Alertmanager webhook 顶层 payload(批量)。"""

    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class AlertOut(BaseModel):
    """告警视图(供主页告警区展示)。"""

    id: str
    fingerprint: str
    service: str | None = None
    severity: AlertSeverity
    summary: str
    source: str
    status: AlertStatus
    fired_at: datetime | None = None
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}
