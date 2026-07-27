"""OIDC 协议交互适配器(块五)。

把与 IdP 的协议交互(授权 URL 构造、授权码换 token、id_token 验证并取 claims)
封装在可注入的 OidcClient 后面:生产用 HttpOidcClient 走真实 discovery + JWKS;
测试注入 FakeOidcClient,不依赖真实 IdP。端点只依赖抽象接口,职责单一可测。
"""

from __future__ import annotations

import urllib.parse
from abc import ABC, abstractmethod

from app.services.oidc_service import OidcClaims


class OidcClient(ABC):
    """OIDC 协议交互抽象。"""

    @abstractmethod
    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """构造 IdP 授权端点 URL(用户浏览器将被重定向到此)。"""

    @abstractmethod
    async def exchange_code(self, *, code: str, redirect_uri: str) -> OidcClaims:
        """用授权码换 token 并验证,返回已验证的身份 claims。"""


class HttpOidcClient(OidcClient):
    """真实 IdP 交互:走 discovery + 授权码交换 + id_token(JWKS)验证。

    MVP 形态:授权 URL 由配置的 issuer 拼装;exchange_code 需在生产接线真实的
    token 端点调用与 JWKS 验签(留到配置真实 issuer 后联调)。此处给出结构与
    授权 URL 构造(可离线测试),token 交换在未配置时明确抛错而非静默返回假身份。
    """

    def __init__(self, settings) -> None:
        self._settings = settings

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        issuer = self._settings.oidc_issuer.rstrip("/")
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._settings.oidc_scopes),
            "state": state,
        }
        return f"{issuer}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OidcClaims:
        raise NotImplementedError(
            "真实 IdP 的授权码交换与 JWKS 验签需在配置 oidc_issuer 后联调实现"
        )
