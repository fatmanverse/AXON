"""第三方身份绑定模型(块五:通用 OIDC 第三方登录)。

一条 UserIdentity 把某个 OIDC 提供方(provider)下的一个主体(subject,IdP 里的
稳定用户标识 `sub`)绑定到本系统的一个 User。本地密码账号与第三方账号并存:
本地账号走 password_hash,第三方账号经此表关联,登录后统一签发本系统 JWT。

设计:
- (provider, subject) 唯一:同一 IdP 的同一主体只绑一个本系统用户,防重复建号。
- subject 用 IdP 的 `sub`(稳定、不随邮箱/用户名变更),不用 email 作绑定键——
  email 可变且可能被 IdP 复用,作绑定键有账号劫持风险。
- user_id 外键级联删除:本系统用户删除时,其第三方绑定一并清理。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


class UserIdentity(Base, TimestampMixin):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # OIDC 提供方标识(如 "keycloak"/"auth0"/"google");支持多 IdP 并存。
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # IdP 里的稳定主体标识(OIDC `sub` claim)。
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # 绑定时的邮箱/显示名快照(仅展示用,不作认证依据)。
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
