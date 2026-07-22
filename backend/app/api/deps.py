"""API 依赖注入:DB 会话、当前用户、权限校验。"""

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.security import TokenClaims, decode_access_token
from app.models.user import User
from app.services.auth_service import AuthService

# auto_error=False:自己抛统一 envelope 的 401,而非 FastAPI 默认体
_bearer = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    return request.app.state.db


async def get_session(
    db: Database = Depends(get_database),
) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session


def get_secret_store(request: Request):
    """凭证保险箱(在 lifespan 构造,存于 app.state)。"""
    return request.app.state.secret_store


def get_ssh_connector(request: Request):
    """SSH 连接工厂;默认 None(由 SSHExecutor 回退到 asyncssh),测试可覆盖。"""
    return getattr(request.app.state, "ssh_connector", None)


def get_prometheus_http_client(request: Request):
    """Prometheus HTTP 客户端;默认 None(由 PrometheusClient 回退到 httpx),测试可覆盖。"""
    return getattr(request.app.state, "prometheus_http_client", None)


def get_pipeline_adapter_provider(request: Request):
    """CI 适配器工厂:入参 service、返回 PipelineAdapter;默认 None(MVP 未配置 CI 时
    部署会明确落 failed),测试可经 app.state.pipeline_adapter_provider 覆写。"""
    return getattr(request.app.state, "pipeline_adapter_provider", None)


def get_health_checker(request: Request):
    """发布后健康检查器(T3.8,§11.1)。默认用 HTTP 探测的生产 prober(命令探测需
    executor,MVP 未接则该类探测优雅失败);测试可经 app.state.health_checker 覆写。
    注入后部署编排在 CI/策略铺开后跑健康检查,不通过则落 failed(可联动自动回滚)。"""
    existing = getattr(request.app.state, "health_checker", None)
    if existing is not None:
        return existing
    from app.services.health_checker import HealthChecker
    from app.services.health_prober import DefaultHealthProber

    return HealthChecker(prober=DefaultHealthProber())


def get_agent_registry(request: Request):
    """AgentGateway 注册表(T4.3):agent_grpc_enabled 时在 lifespan 构造,存于
    app.state;未启用为 None(access_mode=agent 的动作退回 501 占位)。测试可覆写
    app.state.agent_gateway_registry。"""
    return getattr(request.app.state, "agent_gateway_registry", None)


def get_k8s_api_factory(request: Request):
    """k8s client 工厂(T1.10):k8s_enabled 时在 lifespan 加载,存于 app.state;
    未启用为 None(对 k8s 服务的动作明确报 501)。测试可覆写 app.state.k8s_api_factory。"""
    return getattr(request.app.state, "k8s_api_factory", None)


def get_rollout_provider(request: Request):
    """发布策略 RolloutContext 生产工厂(T3.6/T3.7)。按 service.runtime 现组装:
    k8s 用 k8s_api_factory,裸机按 placement 建 executor。测试可经
    app.state.rollout_provider 覆写为 fake;未接线时该 provider 为 None(仅触发 CI)。"""
    existing = getattr(request.app.state, "rollout_provider", None)
    if existing is not None:
        return existing
    from app.services.rollout_provider import build_rollout_provider

    return build_rollout_provider(
        request.app.state.db,
        request.app.state.secret_store,
        request.app.state.settings,
        k8s_api_factory=getattr(request.app.state, "k8s_api_factory", None),
        connector=getattr(request.app.state, "ssh_connector", None),
    )


def get_artifact_deployment_service(request: Request):
    """artifact 直接部署服务(artifact 直发 Task 5)。按需组装 ArtifactDeploymentService,
    注入 db/secrets/connector/agent_registry/k8s_api_factory。测试可经
    app.state.artifact_deployment_service 覆写为 fake。"""
    existing = getattr(request.app.state, "artifact_deployment_service", None)
    if existing is not None:
        return existing
    from app.services.artifact_deployment_service import ArtifactDeploymentService

    return ArtifactDeploymentService(
        request.app.state.db,
        request.app.state.secret_store,
        connector=getattr(request.app.state, "ssh_connector", None),
        agent_registry=getattr(request.app.state, "agent_gateway_registry", None),
        k8s_api_factory=getattr(request.app.state, "k8s_api_factory", None),
    )


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> TokenClaims:
    if credentials is None:
        raise AppError("unauthorized", "缺少认证凭证", status_code=401)
    try:
        return decode_access_token(
            credentials.credentials,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except Exception as exc:
        raise AppError("unauthorized", "认证凭证无效或已过期", status_code=401) from exc


async def get_current_user(
    claims: TokenClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    user = await AuthService(session, settings).get_by_username(claims.subject)
    if user is None or not user.is_active:
        raise AppError("unauthorized", "用户不存在或已停用", status_code=401)
    return user


def require_permission(required: Permission) -> Callable:
    """返回一个依赖:校验当前用户是否具备 required 权限,否则 403。"""

    async def _checker(
        user: User = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> User:
        pset = AuthService.permission_set(user)
        if not pset.allows(required):
            raise AppError(
                "forbidden",
                f"缺少权限: {required}",
                status_code=403,
            )
        return user

    return _checker
