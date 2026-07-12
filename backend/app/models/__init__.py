"""ORM 模型包。

在此汇总导入各模型模块,确保 Base.metadata 收集到全部表,
供 Alembic autogenerate 与建表使用。后续 Epic 新增模型时在此登记。
"""

from app.models.alert import Alert
from app.models.approval import Approval
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.config_delivery import ConfigDelivery
from app.models.deployment import Deployment
from app.models.scan_result import ScanResult
from app.models.server import Server
from app.models.service import Service, ServicePlacement
from app.models.service_config import ServiceConfig
from app.models.task import Task
from app.models.user import Role, RolePermission, User, user_roles

__all__ = [
    "Base",
    "Task",
    "User",
    "Role",
    "RolePermission",
    "user_roles",
    "AuditLog",
    "Server",
    "Service",
    "ServicePlacement",
    "ServiceConfig",
    "ScanResult",
    "Alert",
    "Deployment",
    "ConfigDelivery",
    "Approval",
]
