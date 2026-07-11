"""SQLAlchemy 声明式基类与通用列。"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# JSONB 在 PostgreSQL 上更优;sqlite/其它方言回退到通用 JSON,保证本地测试可跑。
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。Alembic 以 Base.metadata 为迁移目标。"""


class TimestampMixin:
    """给模型加 created_at / updated_at。

    default 走 Python 侧 _utcnow(进程内严格单调),保证 ORM 插入后立即有值、
    同事务内多行时间戳严格可区分(供稳定排序);server_default 作 DB 侧兜底,
    覆盖迁移或原生 SQL 等不经 ORM 的写入路径。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
        nullable=False,
    )
