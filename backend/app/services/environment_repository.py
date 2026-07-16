"""environments 数据访问层(自定义环境管理)。

环境是 services/servers 的 env 段真相源。name 唯一(重名 409);删除不存在 404。
按 name 排序列出,供前端环境下拉与管理页。
"""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.environment import Environment
from app.models.server import Server
from app.models.service import Service
from app.schemas.environment import EnvironmentCreate


class EnvironmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, payload: EnvironmentCreate) -> Environment:
        # 先查重给出明确 409,而非等 flush 抛底层 IntegrityError:唯一约束仍作最终防线
        # (并发下两请求都过了查重),两道一起兜住。
        if await self.get_by_name(payload.name) is not None:
            raise AppError("environment_exists", f"环境 {payload.name!r} 已存在", status_code=409)
        env = Environment(**payload.model_dump())
        self._session.add(env)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AppError(
                "environment_exists", f"环境 {payload.name!r} 已存在", status_code=409
            ) from exc
        return env

    async def get(self, env_id: str) -> Environment:
        env = await self._session.get(Environment, env_id)
        if env is None:
            raise AppError("environment_not_found", "环境不存在", status_code=404)
        return env

    async def get_by_name(self, name: str) -> Environment | None:
        stmt = select(Environment).where(Environment.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(self) -> Sequence[Environment]:
        stmt = select(Environment).order_by(Environment.name)
        return (await self._session.execute(stmt)).scalars().all()

    async def delete(self, env_id: str) -> None:
        env = await self._session.get(Environment, env_id)
        if env is None:
            raise AppError("environment_not_found", "环境不存在", status_code=404)

        # 引用检查(§10.1):env 是 services/servers 的软引用(无 DB 外键),删除前须确认
        # 无服务/服务器仍归属此环境,否则会留下悬空引用(env 字符串还在、environments
        # 表已无对应行),审批判定等按名查环境的逻辑将静默失准。有引用则 409 拒绝。
        service_refs = await self._session.scalar(
            select(func.count()).select_from(Service).where(Service.env == env.name)
        )
        server_refs = await self._session.scalar(
            select(func.count()).select_from(Server).where(Server.environment == env.name)
        )
        if service_refs or server_refs:
            raise AppError(
                "environment_in_use",
                f"环境 {env.name!r} 仍被引用(服务 {service_refs} 个、服务器 {server_refs} 台),"
                "请先迁移或删除后再删除环境",
                status_code=409,
            )

        await self._session.delete(env)
        await self._session.flush()
