"""servers 服务器纳管模型(§14.1)。"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


class AccessMode(StrEnum):
    SSH = "ssh"
    AGENT = "agent"


class AgentStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


def _uuid() -> str:
    return uuid.uuid4().hex


class Server(Base, TimestampMixin):
    __tablename__ = "servers"
    __table_args__ = (
        CheckConstraint(
            "(access_mode = 'ssh' AND ssh_credential_id IS NOT NULL AND agent_id IS NULL) "
            "OR (access_mode = 'agent' AND ssh_credential_id IS NULL AND agent_id IS NOT NULL)",
            name="ck_servers_access_mode_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # 归属环境:引用 environments.name(字符串软关联,非外键——与 services.env 一致的
    # 语义,环境存在性在纳管 API 层软校验)。nullable 兼容历史无环境归属的服务器。
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    access_mode: Mapped[AccessMode] = mapped_column(
        Enum(AccessMode, name="server_access_mode", values_callable=_enum_values), nullable=False
    )
    ssh_credential_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    agent_status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", values_callable=_enum_values),
        nullable=False,
        default=AgentStatus.UNKNOWN,
        index=True,
    )
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
