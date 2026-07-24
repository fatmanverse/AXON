"""认证 API:登录发 JWT、查询当前用户。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import create_access_token
from app.models.user import User
from app.services.auth_service import AccountLockedError, AuthService, InvalidPasswordError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/login")
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    svc = AuthService(session, settings)
    try:
        user = await svc.authenticate(body.username, body.password)
    except AccountLockedError as exc:
        await session.commit()
        raise AppError("account_locked", "账号暂时锁定,请稍后再试", status_code=423) from exc
    if user is None:
        # 失败计数必须在返回 401 前提交,否则依赖会话会回滚本次失败尝试。
        await session.commit()
        raise AppError("unauthorized", "用户名或密码错误", status_code=401)

    roles = [r.name for r in user.roles]
    token = create_access_token(
        subject=user.username,
        secret=settings.jwt_secret,
        roles=roles,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expires_minutes,
        token_version=user.token_version,
    )
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {"username": user.username, "roles": roles},
        }
    )


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return ok(
        {
            "username": user.username,
            "roles": [r.name for r in user.roles],
            "permissions": [str(p) for p in AuthService.permission_set(user).permissions],
        }
    )


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        AuthService.change_password(user, body.current_password, body.new_password)
    except InvalidPasswordError as exc:
        raise AppError("invalid_password", "当前密码错误", status_code=400) from exc
    return ok({"changed": True})


@router.post("/logout")
async def logout(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    AuthService.revoke_sessions(user)
    return ok({"revoked": True})
