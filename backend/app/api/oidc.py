"""OIDC 第三方登录端点(块五:通用 OIDC，自动建号)。

两端点组成授权码流:
- GET /api/auth/oidc/login    构造 IdP 授权 URL + 下发 state(HttpOnly cookie 防 CSRF)
                              后 302 重定向到 IdP。oidc_enabled 关闭时 501。
- GET /api/auth/oidc/callback IdP 回跳:校验 state → 授权码换 token 验证 →
                              OidcService 找/建号并签发本系统 JWT → 重定向回前端带 token。

OidcClient 经 app.state.oidc_client 可注入(测试用 fake,不触真实 IdP)。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings
from app.core.config import Settings
from app.core.errors import AppError
from app.services.oidc_client import HttpOidcClient, OidcClient
from app.services.oidc_service import OidcService

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])

# state cookie 名:回跳时与 query 里的 state 比对，防 CSRF/重放。
_STATE_COOKIE = "oidc_state"


def _require_enabled(settings: Settings) -> None:
    if not settings.oidc_enabled:
        raise AppError("oidc_disabled", "第三方登录未启用", status_code=501)
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise AppError("oidc_misconfigured", "第三方登录配置不完整", status_code=500)


def _client(request: Request, settings: Settings) -> OidcClient:
    injected = getattr(request.app.state, "oidc_client", None)
    return injected if injected is not None else HttpOidcClient(settings)


@router.get("/login")
async def oidc_login(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    _require_enabled(settings)
    state = secrets.token_urlsafe(24)
    url = _client(request, settings).authorization_url(
        state=state, redirect_uri=settings.oidc_redirect_uri
    )
    resp = RedirectResponse(url, status_code=302)
    # state 存 HttpOnly cookie，回跳时比对；短时效，仅覆盖一次登录往返。
    resp.set_cookie(
        _STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/"
    )
    return resp


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    _require_enabled(settings)
    expected = request.cookies.get(_STATE_COOKIE)
    if not expected or not secrets.compare_digest(expected, state):
        raise AppError("oidc_state_mismatch", "登录状态校验失败，请重试", status_code=400)

    claims = await _client(request, settings).exchange_code(
        code=code, redirect_uri=settings.oidc_redirect_uri
    )
    _, token = await OidcService(session, settings).login_with_claims(claims)
    await session.commit()

    # 重定向回前端，token 放 fragment(不进服务端日志/referer)。
    target = f"{settings.oidc_post_login_redirect}#access_token={token}"
    resp = RedirectResponse(target, status_code=302)
    resp.delete_cookie(_STATE_COOKIE, path="/")
    return resp
