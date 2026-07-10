"""FastAPI 应用工厂。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, health, ws
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import RateLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json_logs=settings.log_json, level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
        )
        app.state.db = database

        async def _db_probe() -> None:
            if not await database.ping():
                raise RuntimeError("database unreachable")

        health.register_probe("database", _db_probe)
        try:
            yield
        finally:
            health.unregister_probe("database")
            await database.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # 中间件注册顺序:后加者在外层。期望执行顺序(外→内):
    # SecurityHeaders(给一切响应含 429 盖章)→ RateLimit → RequestContext
    app.add_middleware(RequestContextMiddleware)
    if settings.rate_limit_enabled:
        limiter = RateLimiter(
            capacity=settings.rate_limit_capacity,
            refill_per_sec=settings.rate_limit_refill_per_sec,
        )
        app.add_middleware(
            RateLimitMiddleware,
            limiter=limiter,
            retry_after=settings.rate_limit_retry_after,
        )
    app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(ws.router)

    return app


app = create_app()
