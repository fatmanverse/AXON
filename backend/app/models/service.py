"""services 与 service_placements 模型(§14.2)。"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONVariant, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class ServiceEnvironment(StrEnum):
    """历史内置环境名的便捷常量(dev/staging/prod)。

    环境已改为用户自建(见 app/models/environment.py 的 Environment 表),services.env
    是纯字符串,可为任意已创建的环境名——不再受此枚举约束。本枚举仅作 seed / 测试 /
    调用方书写常用环境名的便捷常量保留(StrEnum 成员即字符串,传给 str 字段兼容)。
    是否走审批由 Environment.requires_approval 决定,不再由环境名硬编码判定。
    """

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Runtime(StrEnum):
    K8S = "k8s"
    DOCKER = "docker"
    SYSTEMD = "systemd"
    PROCESS = "process"
    CLOUD_FN = "cloud-fn"


class ReloadMode(StrEnum):
    RELOAD = "reload"
    RESTART = "restart"


class ObservedStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


class Service(Base, TimestampMixin):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("name", "env", name="uq_services_name_env"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # env 存环境 name 字符串(不再是 Enum):环境由用户自建(Environment 表),可为任意
    # 已创建的环境名。保持既有字符串语义贯穿 deployments/configs/审计/权限,环境是否存在
    # 由纳管/建服务时软校验,是否走审批由 Environment.requires_approval 决定。
    env: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    runtime: Mapped[Runtime] = mapped_column(
        Enum(Runtime, name="service_runtime", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    runtime_ref: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    desired_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_deployment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reload_mode: Mapped[ReloadMode] = mapped_column(
        Enum(ReloadMode, name="service_reload_mode", values_callable=_enum_values),
        nullable=False,
        default=ReloadMode.RESTART,
    )
    health_check: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    # 本地构建默认配置(照 health_check 先例,可空 JSON):承载「填仓库即构建」所需的
    # 缺省项——repo_url/git_ref/test_command/build_command/artifact_type(generic|docker)
    # 及各形态坐标(generic 的 output_path、docker 的 image_name/dockerfile/registry_id)。
    # 每次触发构建可用请求体覆盖其中的 git_ref/version。未配置则该服务不支持本地构建。
    build_config: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    placements: Mapped[list["ServicePlacement"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )


class ServicePlacement(Base):
    __tablename__ = "service_placements"
    __table_args__ = (
        UniqueConstraint("service_id", "server_id", name="uq_service_placements_service_server"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    observed_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_status: Mapped[ObservedStatus] = mapped_column(
        Enum(ObservedStatus, name="placement_observed_status", values_callable=_enum_values),
        nullable=False,
        default=ObservedStatus.UNKNOWN,
        index=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    service: Mapped[Service] = relationship(back_populates="placements")
