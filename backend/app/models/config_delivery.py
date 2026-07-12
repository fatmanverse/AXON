"""config_deliveries 配置下发记录模型(§14.5)。

一个配置版本下发到多个放置点(service_placements),逐目标一条记录:
可能部分成功、部分失败,单布尔标志表达不了这种多目标半成功态,故独立成表。
status 三态:pending(已建待下发)→ success / failed。result/error 承载
目标机返回的摘要,供下发页逐目标展示。
"""

import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class ConfigDelivery(Base, TimestampMixin):
    __tablename__ = "config_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 下发的是哪个配置版本(service_configs.id)
    config_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("service_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 下发到哪个放置点(service_placements.id)
    placement_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("service_placements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="config_delivery_status", values_callable=_enum_values),
        nullable=False,
        default=DeliveryStatus.PENDING,
        index=True,
    )
    # 目标机返回的结果摘要(如 reload 输出);失败时错误摘要入 error
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
