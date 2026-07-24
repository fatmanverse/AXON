"""build_nodes 构建节点模型(方案 A:控制面主机=首个构建节点,可注册更多)。

构建节点是一台 SSH 可达、装有构建工具链(git + docker/语言 SDK)的机器,负责
`git clone → 测试 → 构建 → 产出制品`。控制面所在宿主机是默认的首个节点,后续可
再注册更多节点横向扩展构建并发。

节点很可能就是一台已纳管的 Server(通过 server_id 软引用挂靠,复用其 SSH 凭证与
连接参数);也允许是一台外部专用构建机(server_id 为空,自带 host/凭证引用)。
构建执行统一走 executor_factory,与生命周期/配置下发同一条 SSH 通道。
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class BuildNodeStatus(StrEnum):
    """构建节点健康态。镜像 AgentStatus 风格,存小写值。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class BuildNode(Base, TimestampMixin):
    __tablename__ = "build_nodes"
    # 挂靠的已纳管服务器唯一:一台 Server 至多登记为一个构建节点,避免重复挂靠。
    # server_id 为空(外部专用构建机)时不参与该唯一判定(NULL 不判重)。
    __table_args__ = (UniqueConstraint("server_id", name="uq_build_nodes_server"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    # 挂靠一台已纳管服务器时软引用其 id(复用 SSH 凭证);外部专用构建机留空。
    server_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 外部专用构建机(server_id 为空)时自带连接信息;挂靠已纳管机时可留空,连接
    # 参数从 Server 取。凭证一律只存保险箱引用,不落明文(§13)。
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_credential_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[BuildNodeStatus] = mapped_column(
        Enum(BuildNodeStatus, name="build_node_status", values_callable=_enum_values),
        nullable=False,
        default=BuildNodeStatus.UNKNOWN,
        index=True,
    )
    # 工具链声明(如 {"go": "1.22", "node": "20", "docker": true}),供构建调度选节点。
    labels: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    # 该节点允许的并发构建数;调度时据此限流。
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
