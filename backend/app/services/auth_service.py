"""认证服务:用户创建、登录校验、角色权限解析、种子数据。"""

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
    ],
    "viewer": [
        "service:*:read",
        "server:*:read",
        "config:*:read",
        "deployment:*:read",
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
        if not verify_password(password, user.password_hash):
            return None
        return user

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
