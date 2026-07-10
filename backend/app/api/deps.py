"""API 依赖注入:DB 会话、当前用户、权限校验。"""

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.security import TokenClaims, decode_access_token
from app.models.user import User
from app.services.auth_service import AuthService

# auto_error=False:自己抛统一 envelope 的 401,而非 FastAPI 默认体
_bearer = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    return request.app.state.db


async def get_session(
    db: Database = Depends(get_database),
) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> TokenClaims:
    if credentials is None:
        raise AppError("unauthorized", "缺少认证凭证", status_code=401)
    try:
        return decode_access_token(
            credentials.credentials,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except Exception as exc:
        raise AppError("unauthorized", "认证凭证无效或已过期", status_code=401) from exc


async def get_current_user(
    claims: TokenClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    user = await AuthService(session, settings).get_by_username(claims.subject)
    if user is None or not user.is_active:
        raise AppError("unauthorized", "用户不存在或已停用", status_code=401)
    return user


def require_permission(required: Permission) -> Callable:
    """返回一个依赖:校验当前用户是否具备 required 权限,否则 403。"""

    async def _checker(
        user: User = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> User:
        pset = AuthService.permission_set(user)
        if not pset.allows(required):
            raise AppError(
                "forbidden",
                f"缺少权限: {required}",
                status_code=403,
            )
        return user

    return _checker
