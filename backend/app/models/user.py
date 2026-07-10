"""用户 / 角色 / 角色-权限模型(§13 RBAC)。

关系:User N—N Role,Role 1—N RolePermission(权限三元组字符串)。
角色内置若干(admin / operator / developer / viewer),权限点 MVP 可粗,
以通配表达,后续细化只增 RolePermission 行,不改调用方。
"""

import uuid

from sqlalchemy import Column as SAColumn
from sqlalchemy import ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


user_roles = Table(
    "user_roles",
    Base.metadata,
    SAColumn("user_id", String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    SAColumn("role_id", String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_role_permission"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 权限三元组字符串 resource:env:action(见 app.core.permissions)
    permission: Mapped[str] = mapped_column(String(128), nullable=False)

    role: Mapped[Role] = relationship(back_populates="permissions")
