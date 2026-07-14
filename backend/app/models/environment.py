"""environments 环境模型(自定义环境管理)。

取代此前写死的 ServiceEnvironment 枚举(dev/staging/prod):环境由用户自建,
无预置数据。每个环境自带 requires_approval 开关,取代原先散落在 API 里对
`env == 'prod'` 的硬编码审批判定——是否走审批完全由环境自身声明,而非环境名。

服务(services)、服务器(servers)、部署(deployments)、配置、审计、权限的
env 段均以环境 name 字符串引用本表(保持既有字符串语义,不改为外键 id),
本表是环境是否存在、是否需要审批的唯一真相源。
"""

import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # name 是环境的稳定标识,贯穿 services/servers/deployments/审计/权限的 env 段。
    # 唯一且不可与既有环境重名。
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 是否需要审批:True 时该环境的 deploy/delete/rollback 走 pending 审批流(§10.2)。
    # 取代旧的 `env == prod` 硬编码判定。
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
