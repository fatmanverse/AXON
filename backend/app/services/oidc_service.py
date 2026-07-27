"""OIDC 第三方登录账号服务(块五)。

职责:拿到 IdP 已验证的身份 claims 后,把它落成本系统的登录态——
按 (provider, subject) 找已有绑定;没有则自动建号(方案:自动建号 + 最小
只读角色)并建立绑定;最后复用本系统既有 create_access_token 签发 JWT,
与本地密码登录发的是同一种令牌,下游鉴权无差别。

边界:本模块不做 OIDC 协议交互(discovery / code 交换 / JWKS 验签)——那由
可注入的 OidcClient 负责(便于单测用 fake 顶替,不依赖真实 IdP)。本模块只吃
"已验证的 claims",专注账号映射与令牌签发,职责单一、可独立测试。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.services.auth_service import AuthService


@dataclass(frozen=True)
class OidcClaims:
    """IdP 已验证的身份信息(经 OidcClient 校验后的结果)。"""

    subject: str
    email: str | None = None
    preferred_username: str | None = None


class OidcConfigError(Exception):
    """OIDC 未启用或配置缺失时抛出,由端点转 501/500。"""


class OidcService:
    """把已验证 OIDC claims 映射为本系统登录态(找/建号 + 签发 JWT)。"""

    def __init__(self, session: AsyncSession, settings) -> None:
        self._session = session
        self._settings = settings

    async def login_with_claims(self, claims: OidcClaims) -> tuple[User, str]:
        """按 claims 找/建用户并签发 JWT。返回 (user, access_token)。

        流程:
        1. 按 (provider, subject) 找 UserIdentity。
        2. 命中则取绑定 user;未命中则自动建号 + 建绑定。
        3. 复用 create_access_token 签发本系统 JWT(与本地登录同款)。
        """
        provider = self._settings.oidc_provider
        result = await self._session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == provider,
                UserIdentity.subject == claims.subject,
            )
        )
        identity = result.scalar_one_or_none()

        if identity is not None:
            user = await self._session.get(User, identity.user_id)
            if user is None:
                # 绑定悬空(用户被删):按孤儿处理,重新建号并改绑。
                user = await self._provision_user(claims)
                identity.user_id = user.id
            await self._session.flush()
            return user, self._issue_token(user)

        user = await self._provision_user(claims)
        self._session.add(
            UserIdentity(
                user_id=user.id,
                provider=provider,
                subject=claims.subject,
                email=claims.email,
            )
        )
        await self._session.flush()
        return user, self._issue_token(user)

    async def _provision_user(self, claims: OidcClaims) -> User:
        """自动建号:唯一 username + 不可登录哨兵密码 + 最小只读角色。"""
        username = await self._unique_username(claims)
        # 第三方账号无本地密码:存一个随机不可逆哨兵,password 登录永不匹配。
        sentinel = hash_password(uuid.uuid4().hex)
        user = User(username=username, password_hash=sentinel)
        role_name = self._settings.oidc_default_role
        role = await AuthService(self._session, self._settings)._get_or_create_role(role_name)
        user.roles.append(role)
        self._session.add(user)
        await self._session.flush()
        return user

    async def _unique_username(self, claims: OidcClaims) -> str:
        """优先用 preferred_username,其次 email 前缀,再兜底 provider+sub;冲突加后缀。"""
        base = (
            claims.preferred_username
            or (claims.email or "").split("@")[0]
            or f"{self._settings.oidc_provider}-{claims.subject}"
        )
        candidate = base
        suffix = 1
        while True:
            existing = await self._session.execute(
                select(User).where(User.username == candidate)
            )
            if existing.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    def _issue_token(self, user: User) -> str:
        roles = [r.name for r in user.roles]
        return create_access_token(
            subject=user.username,
            secret=self._settings.jwt_secret,
            roles=roles,
            algorithm=self._settings.jwt_algorithm,
            expires_minutes=self._settings.jwt_expires_minutes,
            token_version=user.token_version,
        )
