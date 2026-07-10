"""SQLAlchemy 声明式基类与通用列。"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# JSONB 在 PostgreSQL 上更优;sqlite/其它方言回退到通用 JSON,保证本地测试可跑。
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。Alembic 以 Base.metadata 为迁移目标。"""


class TimestampMixin:
    """给模型加 created_at / updated_at,由数据库侧维护。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
