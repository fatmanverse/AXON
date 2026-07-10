"""审计日志模型(§14.7)。

仅追加、不可篡改:所有写操作(部署/启停/删除/改配置)落一条记录,
记录 actor/action/target/env/before/after/result/ip/ua,可回放。
表本身不提供 update/delete 语义(应用层与 AuditService 均不暴露改删)。
"""

import uuid
from enum import StrEnum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


def _uuid() -> str:
    return uuid.uuid4().hex


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(255), index=True)
    target: Mapped[str] = mapped_column(String(255), index=True)
    env: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    result: Mapped[AuditResult] = mapped_column(Enum(AuditResult, name="audit_result"))
    before: Mapped[dict | None] = mapped_column(JSONVariant, default=None)
    after: Mapped[dict | None] = mapped_column(JSONVariant, default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    ua: Mapped[str | None] = mapped_column(String(512), default=None)
