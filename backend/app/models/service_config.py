"""service_configs 配置版本模型(§14.5)。

每个服务的配置独立版本化:每次修改生成新版本(version 按 service 自增),
记录 who/when/what,支持 diff 与回滚。is_current 标记当前生效版本——同一
service 至多一条 is_current=True。敏感值存保险箱,配置里只存 ${secret:xxx}
引用(§12.2),本表不落明文密钥。

血缘(§14.5):content_hash 是内容 SHA-256,用于快速判等与跳过重复下发;
diff_from 指向本版派生的上一版(创建时的 current 版 id),串起版本血缘链。
"""

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


class ConfigFormat(StrEnum):
    ENV = "env"
    YAML = "yaml"
    PROPERTIES = "properties"
    JSON = "json"


def _uuid() -> str:
    return uuid.uuid4().hex


class ServiceConfig(Base, TimestampMixin):
    __tablename__ = "service_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # version 按 service 自增(仓储层保证),非全局自增
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 内容 SHA-256(§14.5):相同则目标机内容未变,可跳过重复下发;亦供快速判等。
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 下发到目标机的绝对路径(§12.2 推模式);为空则该版本不可下发(仅暂存)
    target_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    format: Mapped[ConfigFormat] = mapped_column(
        Enum(ConfigFormat, name="service_config_format", values_callable=_enum_values),
        nullable=False,
        default=ConfigFormat.ENV,
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 血缘(§14.5):本版派生自哪一版(创建时的 current 版 id);首版为 None。
    diff_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 当前生效版本标记;同一 service 至多一条 True(仓储层切换时保证互斥)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # version 在服务内唯一:同一 service 的 version 不可重复(仓储层自增保证连续)
    __table_args__ = (
        UniqueConstraint("service_id", "version", name="uq_service_configs_service_version"),
    )
