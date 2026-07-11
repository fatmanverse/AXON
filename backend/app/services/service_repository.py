"""services 与 service_placements 数据访问层(T1.2)。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.models.service import (
    ObservedStatus,
    Runtime,
    Service,
    ServiceEnvironment,
    ServicePlacement,
)
from app.schemas.service import PlacementCreate, ServiceCreate


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_service(self, payload: ServiceCreate) -> Service:
        service = Service(**payload.model_dump())
        self._session.add(service)
        await self._session.flush()
        return service

    async def get_service(self, service_id: str) -> Service:
        service = await self._session.get(Service, service_id)
        if service is None:
            raise AppError("service_not_found", "服务不存在", status_code=404)
        return service

    async def get_by_name_env(self, name: str, env: str) -> Service | None:
        """按 (name, env) 定位服务(webhook 上报带的是服务名而非 id)。

        (name, env) 有唯一约束(uq_services_name_env),至多一条;查无返回 None,
        由调用方决定 404 还是忽略。
        """
        stmt = select(Service).where(Service.name == name, Service.env == env)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_services(
        self,
        *,
        env: ServiceEnvironment | None = None,
        runtime: Runtime | None = None,
    ) -> Sequence[Service]:
        """列出服务(可按 env/runtime 过滤),预加载 placements 供计数。

        用 selectinload 一次性带出 placements,避免列表视图逐个懒加载 N+1;
        按 name 稳定排序,便于前端展示与测试断言。
        """
        stmt = select(Service).options(selectinload(Service.placements))
        if env is not None:
            stmt = stmt.where(Service.env == env)
        if runtime is not None:
            stmt = stmt.where(Service.runtime == runtime)
        stmt = stmt.order_by(Service.name)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def create_placement(self, payload: PlacementCreate) -> ServicePlacement:
        service = await self.get_service(payload.service_id)
        if service.runtime != Runtime.K8S and payload.server_id is None:
            raise ValueError("非 k8s 服务的 placement 必须提供 server_id")

        placement = ServicePlacement(**payload.model_dump())
        self._session.add(placement)
        await self._session.flush()
        return placement

    async def list_placements(self, service_id: str) -> Sequence[ServicePlacement]:
        # placement 无天然时间序(§14.2 未设时间戳),按 server_id 稳定排序;
        # k8s 无 server 的放置 server_id 为 NULL,排在前部。
        result = await self._session.execute(
            select(ServicePlacement)
            .where(ServicePlacement.service_id == service_id)
            .order_by(ServicePlacement.server_id)
        )
        return result.scalars().all()

    async def list_placements_on_servers(self) -> Sequence[ServicePlacement]:
        """列出所有落在某台服务器上的放置(server_id 非空)。

        供状态采集器(T1.12)遍历:k8s 无 server 的放置由集群侧实时查,不走
        SSH 轮询,故这里排除 server_id 为空的行。按 server_id 稳定排序。
        """
        result = await self._session.execute(
            select(ServicePlacement)
            .where(ServicePlacement.server_id.is_not(None))
            .order_by(ServicePlacement.server_id)
        )
        return result.scalars().all()

    async def update_observed(
        self,
        placement_id: str,
        *,
        status: ObservedStatus,
        version: str | None = None,
        last_seen_at: datetime | None = None,
    ) -> ServicePlacement:
        """回写一个放置的观测状态(T1.12 采集结果)。

        采集器不改期望态,只更新 observed_*;version 为 None 时保留原值(探测
        通道未必能拿到版本),last_seen_at 显式传入便于测试确定性断言。
        """
        placement = await self._session.get(ServicePlacement, placement_id)
        if placement is None:
            raise AppError("placement_not_found", "服务放置不存在", status_code=404)
        placement.observed_status = status
        if version is not None:
            placement.observed_version = version
        if last_seen_at is not None:
            placement.last_seen_at = last_seen_at
        await self._session.flush()
        return placement
