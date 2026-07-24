"""认证服务:用户创建、登录校验、角色权限解析、种子数据。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.permissions import Permission, PermissionSet, parse_permission
from app.core.security import hash_password, verify_password
from app.models.user import Role, RolePermission, User

# 内置角色 → 权限三元组(MVP 粗粒度,以通配表达)。
# resource:env:action;prod 高危动作只授予 admin/operator。
DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ["*:*:*"],
    "operator": [
        "service:dev:*",
        "service:staging:*",
        "service:prod:*",
        "server:*:*",
        "config:*:*",
        "deployment:*:*",
        "approval:*:*",
        "environment:*:*",
        "buildnode:*:*",
    ],
    "developer": [
        "service:dev:*",
        "service:staging:*",
        "service:prod:read",
        "server:*:read",
        "config:dev:*",
        "config:staging:*",
        "deployment:dev:*",
        "deployment:staging:*",
        "environment:*:read",
    ],
    "viewer": [
        "service:*:read",
        "server:*:read",
        "config:*:read",
        "deployment:*:read",
        "environment:*:read",
    ],
}


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def _get_or_create_role(self, name: str) -> Role:
        result = await self._session.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is not None:
            return role
        role = Role(name=name)
        for perm in DEFAULT_ROLE_PERMISSIONS.get(name, []):
            role.permissions.append(RolePermission(permission=perm))
        self._session.add(role)
        await self._session.flush()
        return role

    async def create_user(self, username: str, password: str, *, roles: list[str]) -> User:
        role_objs = [await self._get_or_create_role(name) for name in roles]
        user = User(username=username, password_hash=hash_password(password), roles=role_objs)
        self._session.add(user)
        await self._session.flush()
        return user

    async def authenticate(self, username: str, password: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            return None
        now = datetime.now(UTC)
        locked_until = user.locked_until
        if locked_until is not None and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until is not None and locked_until > now:
            raise AccountLockedError
        if not verify_password(password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= self._settings.auth_max_failed_attempts:
                user.locked_until = now + timedelta(minutes=self._settings.auth_lockout_minutes)
                await self._session.flush()
                raise AccountLockedError
            await self._session.flush()
            return None
        user.failed_login_count = 0
        user.locked_until = None
        await self._session.flush()
        return user

    @staticmethod
    def change_password(user: User, current_password: str, new_password: str) -> None:
        if not verify_password(current_password, user.password_hash):
            raise InvalidPasswordError
        user.password_hash = hash_password(new_password)
        user.failed_login_count = 0
        user.locked_until = None
        user.token_version += 1

    @staticmethod
    def revoke_sessions(user: User) -> None:
        user.token_version += 1

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    @staticmethod
    def permission_set(user: User) -> PermissionSet:
        perms: list[Permission] = []
        for role in user.roles:
            for rp in role.permissions:
                perms.append(parse_permission(rp.permission))
        return PermissionSet(perms)


class AccountLockedError(Exception):
    """登录失败次数达到阈值或账号仍在锁定窗口内。"""


class InvalidPasswordError(Exception):
    """当前密码校验失败。"""
