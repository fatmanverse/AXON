"""网关中间件:请求追踪、安全响应头、全局限流。"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger
from app.core.ratelimit import RateLimiter
from app.core.responses import fail

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger("http")

# 安全响应头:纵深防御,所有响应统一附带
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-XSS-Protection": "0",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info("request_completed", elapsed_ms=elapsed_ms)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """给所有响应附带安全头(含错误与限流响应)。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


def _client_key(request: Request) -> str:
    """限流分桶键:优先反向代理透传的真实 IP,回退到直连 peer。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流:按客户端 IP 令牌桶,超限返回统一 envelope 的 429。

    exempt_prefixes 命中的路径跳过限流:webhook 走自身 HMAC 鉴权(§8.3),
    机器对机器的正常突发上报不应被用户级 IP 限流误伤。
    """

    def __init__(
        self,
        app,
        limiter: RateLimiter,
        retry_after: int = 1,
        exempt_prefixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._retry_after = retry_after
        self._exempt_prefixes = tuple(exempt_prefixes)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._exempt_prefixes):
            return await call_next(request)
        if not self._limiter.allow(_client_key(request)):
            log.warning("rate_limited", client=_client_key(request))
            response = JSONResponse(
                status_code=429,
                content=fail("rate_limited", "请求过于频繁,请稍后再试"),
            )
            response.headers["Retry-After"] = str(self._retry_after)
            return response
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """请求体大小限制(T0.12):超过上限返回 413,防超大请求体耗尽内存。

    优先信任 Content-Length 头快速拒绝;缺失时(分块传输)读取 body 后按实际
    字节数校验。校验通过则不改变下游读取 body 的能力(Starlette 缓存 body)。
    """

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    def _too_large(self) -> Response:
        return JSONResponse(
            status_code=413,
            content=fail("request_too_large", "请求体超过大小上限"),
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_bytes:
                    return self._too_large()
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content=fail("bad_content_length", "非法 Content-Length"),
                )
        else:
            # 无 Content-Length(分块传输):读出 body 按实际字节校验。
            body = await request.body()
            if len(body) > self._max_bytes:
                return self._too_large()
        return await call_next(request)
