"""approvals 生产审批模型(§10.2 / §13)。

prod 高危操作(deploy / delete / rollback)不直接执行,先落一条 pending 审批,
记录发起人与执行所需参数(payload);有 approve 权限者批准后才真正建 task 执行,
拒绝则关闭。审批本身入审计(§13),形成"谁发起、谁批准/拒绝"的可追溯链路。

状态机:pending → approved / rejected(二者均为终态,只前进不回退)。
approved 之后由上层建 task 执行,执行结果落在 task 上,不改审批记录状态。
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class ApprovalAction(StrEnum):
    """可进审批的高危动作类型。"""

    DEPLOY = "deploy"
    DELETE = "delete"
    ROLLBACK = "rollback"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 待执行动作与目标服务(执行时按 action + service_id + payload 还原原操作)
    action: Mapped[ApprovalAction] = mapped_column(
        Enum(ApprovalAction, name="approval_action", values_callable=_enum_values),
        nullable=False,
    )
    service_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 长度与 environments.name / services.env 对齐(64),避免自定义长环境名截断。
    env: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 执行该动作所需的参数(如 deploy 的 version/strategy/git_sha),批准后据此建 task
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", values_callable=_enum_values),
        nullable=False,
        default=ApprovalStatus.PENDING,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    # 批准/拒绝者与时刻(审批人不可与发起人同一人由上层策略决定,模型不强制)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 批准后建出的 task id(供前端从审批跳到执行进度);拒绝时为空
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
