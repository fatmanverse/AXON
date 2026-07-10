"""认证 API:登录发 JWT、查询当前用户。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import create_access_token
from app.models.user import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    svc = AuthService(session, settings)
    user = await svc.authenticate(body.username, body.password)
    if user is None:
        raise AppError("unauthorized", "用户名或密码错误", status_code=401)

    roles = [r.name for r in user.roles]
    token = create_access_token(
        subject=user.username,
        secret=settings.jwt_secret,
        roles=roles,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expires_minutes,
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
