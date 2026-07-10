"""FastAPI 应用工厂。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, health
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


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

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)

    return app


app = create_app()
