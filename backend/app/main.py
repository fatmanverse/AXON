"""FastAPI 应用工厂。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.adapters.agent_gateway_registry import AgentGatewayRegistry
from app.api import (
    alerts,
    approvals,
    auth,
    builds,
    deployments,
    dist,
    environments,
    health,
    metrics,
    servers,
    services,
    tasks,
    webhooks,
    ws,
)
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import RateLimiter, RedisRateLimiter
from app.core.secrets import build_secret_store
from app.core.ws_hub import RedisHub, configure_hub
from app.services.agent_connection import AgentConnectionManager
from app.services.agent_grpc_server import AgentGrpcServer
from app.services.k8s_client import (
    build_k8s_api_factory,
    build_k8s_custom_objects_api_factory,
)
from app.services.pipeline_provider import build_pipeline_provider
from app.services.redis_agent_connection import RedisAgentConnectionManager


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_runtime()
    configure_logging(json_logs=settings.log_json, level=settings.log_level)
    redis_client: Redis | None = None
    redis_hub: RedisHub | None = None
    if settings.coordination_backend == "redis":
        redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
        redis_hub = RedisHub(redis_client)
        configure_hub(redis_hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
        )
        app.state.db = database
        app.state.redis = redis_client
        if redis_hub is not None:
            await redis_hub.start()
        app.state.secret_store = build_secret_store(settings)
        # SSH 连接工厂:默认用 asyncssh.connect;测试可覆写 app.state.ssh_connector
        if not hasattr(app.state, "ssh_connector"):
            app.state.ssh_connector = None
        # CI pipeline provider 生产装配(T2.7):按 settings.pipeline_config 构造 Jenkins/
        # GitLab adapter;配置为空则 provider 恒返回 None(部署报"未配置 CI",不 500)。
        # 测试可预置 app.state.pipeline_adapter_provider 覆写为 fake,故仅在未设置时装。
        if not hasattr(app.state, "pipeline_adapter_provider"):
            app.state.pipeline_adapter_provider = build_pipeline_provider(
                settings.pipeline_config, app.state.secret_store
            )

        # k8s client 工厂生产装配(T1.10/T3.6):k8s_enabled 时启动加载集群连接一次,
        # 供 k8s 服务的生命周期动作与发布策略铺开使用;未开启则为 None(k8s 动作报 501)。
        # 测试可预置 app.state.k8s_api_factory 覆写为 fake,故仅在未设置时装。
        if not hasattr(app.state, "k8s_api_factory"):
            app.state.k8s_api_factory = await build_k8s_api_factory(settings)
        if not hasattr(app.state, "k8s_custom_objects_api_factory"):
            app.state.k8s_custom_objects_api_factory = build_k8s_custom_objects_api_factory(
                settings
            )

        # Agent gRPC server(T4.1,§15.5):按开关起。与 AgentGateway 共享同一
        # AgentConnectionManager(挂 app.state),命令下发与 ACK 回传才对得上。
        # 默认关闭(纯 SSH 部署);开启后 Agent 可主动外连建双向流。
        grpc_server: AgentGrpcServer | None = None
        agent_manager: AgentConnectionManager | None = None
        if settings.agent_grpc_enabled:
            if redis_client is not None:
                manager = RedisAgentConnectionManager(
                    redis_client,
                    heartbeat_timeout=settings.agent_heartbeat_timeout_sec,
                )
            else:
                manager = AgentConnectionManager(
                    heartbeat_timeout=settings.agent_heartbeat_timeout_sec
                )
            await manager.start()
            agent_manager = manager
            app.state.agent_connection_manager = manager
            # AgentGateway 注册表(T4.3):按 agent_id 复用 gateway,避免每次动作重复
            # 注册 manager 回调导致内存泄漏。access_mode=agent 的服务器动作经此走真实
            # 命令下发路径(替换 T1.5 占位)。仅 gRPC 开启时才有,纯 SSH 部署为 None。
            if not hasattr(app.state, "agent_gateway_registry"):
                app.state.agent_gateway_registry = AgentGatewayRegistry(
                    manager,
                    ack_timeout=settings.agent_heartbeat_timeout_sec,
                    artifact_chunk_bytes=settings.agent_artifact_chunk_bytes,
                    artifact_max_bytes=settings.agent_artifact_max_bytes,
                )
            grpc_server = AgentGrpcServer(
                manager,
                host=settings.agent_grpc_host,
                port=settings.agent_grpc_port,
                tls_enabled=settings.agent_grpc_tls_enabled,
                server_cert_file=settings.agent_grpc_server_cert_file,
                server_key_file=settings.agent_grpc_server_key_file,
                client_ca_file=settings.agent_grpc_client_ca_file,
                revoked_agent_ids=frozenset(settings.agent_grpc_revoked_agent_ids),
            )
            await grpc_server.start()

        async def _db_probe() -> None:
            if not await database.ping():
                raise RuntimeError("database unreachable")

        health.register_probe("database", _db_probe)
        if redis_client is not None:

            async def _redis_probe() -> None:
                if not await redis_client.ping():
                    raise RuntimeError("redis unreachable")

            health.register_probe("redis", _redis_probe)
        try:
            yield
        finally:
            health.unregister_probe("database")
            if redis_client is not None:
                health.unregister_probe("redis")
            if grpc_server is not None:
                await grpc_server.stop()
            if agent_manager is not None:
                await agent_manager.stop()
            if redis_hub is not None:
                await redis_hub.stop()
            await database.dispose()
            if redis_client is not None:
                await redis_client.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # 中间件注册顺序:后加者在外层。期望执行顺序(外→内):
    # CORS → SecurityHeaders → RateLimit → BodySizeLimit → RequestContext
    app.add_middleware(RequestContextMiddleware)
    # 请求体大小限制(T0.12):超过 max_request_body_bytes 的请求早拒 413,
    # 防止超大 body 拖垮 worker。放内层(RequestContext 之外),在业务前拦。
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
    )
    if settings.rate_limit_enabled:
        limiter = (
            RedisRateLimiter(
                redis_client,
                capacity=settings.rate_limit_capacity,
                refill_per_sec=settings.rate_limit_refill_per_sec,
            )
            if redis_client is not None
            else RateLimiter(
                capacity=settings.rate_limit_capacity,
                refill_per_sec=settings.rate_limit_refill_per_sec,
            )
        )
        # webhook 路由走自身 HMAC 鉴权(§8.3),按源节流,不应与用户请求同桶被误限,
        # 故按前缀豁免全局限流(验收:webhook 不被误限流)。
        app.add_middleware(
            RateLimitMiddleware,
            limiter=limiter,
            retry_after=settings.rate_limit_retry_after,
            exempt_prefixes=settings.rate_limit_exempt_prefixes,
        )
    app.add_middleware(SecurityHeadersMiddleware)
    # CORS 白名单(T0.12):只放行 settings.cors_origins,置于最外层使预检与
    # 实际响应都带 CORS 头。凭证放行按需(前端带 JWT 走 Authorization 头)。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(dist.router)
    app.include_router(auth.router)
    app.include_router(environments.router)
    app.include_router(servers.router)
    app.include_router(services.router)
    app.include_router(builds.router)
    app.include_router(deployments.router)
    app.include_router(tasks.router)
    app.include_router(metrics.router)
    app.include_router(webhooks.router)
    app.include_router(alerts.router)
    app.include_router(approvals.router)
    app.include_router(ws.router)

    return app


app = create_app()
