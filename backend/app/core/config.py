"""应用配置:全部从环境变量加载(前缀 YIMAI_)。"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YIMAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "一脉 Axon 控制面"
    env: Env = "dev"
    debug: bool = False

    # 日志
    log_json: bool = True
    log_level: str = "INFO"

    # CORS 白名单(T0.12 使用)
    cors_origins: list[str] = ["http://localhost:5173"]

    # 数据库:生产走 asyncpg,本地/测试可用 aiosqlite
    database_url: str = "postgresql+asyncpg://yimai:yimai@localhost:5432/yimai"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # 认证(JWT)
    jwt_secret: str = "CHANGE-ME-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480

    # 凭证保险箱(§13):local(Fernet)| vault
    secret_backend: Literal["local", "vault"] = "local"
    secret_master_key: str = ""  # local 后端主密钥(生产由 KMS 注入,不落配置文件)
    vault_addr: str = ""
    vault_token: str = ""

    # 任务队列(Celery + Redis)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False

    # 网关限流(T0.12):MVP 用进程内令牌桶,后续可换 Redis 分布式实现
    rate_limit_enabled: bool = True
    rate_limit_capacity: int = 120  # 桶容量(突发上限)
    rate_limit_refill_per_sec: float = 20.0  # 每秒补充速率(稳态 QPS)
    rate_limit_retry_after: int = 1  # 429 响应的 Retry-After 秒数

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
