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
    """全局限流:按客户端 IP 令牌桶,超限返回统一 envelope 的 429。"""

    def __init__(self, app, limiter: RateLimiter, retry_after: int = 1) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._retry_after = retry_after

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._limiter.allow(_client_key(request)):
            log.warning("rate_limited", client=_client_key(request))
            response = JSONResponse(
                status_code=429,
                content=fail("rate_limited", "请求过于频繁,请稍后再试"),
            )
            response.headers["Retry-After"] = str(self._retry_after)
            return response
        return await call_next(request)
