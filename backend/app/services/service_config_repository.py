"""service_configs 配置版本数据访问层(T2.6,§12.1/§14.5)。

每次改配置生成新版本(version 按 service 自增),新版自动接管 is_current,
旧版置 False(同一 service 至多一条 current,互斥)。activate 用于配置回滚:
把 current 切回历史某版。版本查无抛 404。

血缘(§14.5):每版记 content_hash(内容 SHA-256)与 diff_from(派生自哪一版,
指向创建时的当前生效版)。content_hash 供快速判等与跳过重复下发;diff_from 串起
"从哪一版改到这一版"的版本链,支撑服务端 diff 与追溯。
"""

import hashlib
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.service_config import ConfigFormat, ServiceConfig


class ServiceConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _hash_content(content: str) -> str:
        """内容指纹:SHA-256 十六进制。相同内容→相同 hash,用于判等与跳过重复下发。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def create_version(
        self,
        *,
        service_id: str,
        content: str = "",
        format: ConfigFormat = ConfigFormat.ENV,
        created_by: str | None = None,
        comment: str | None = None,
        target_path: str | None = None,
    ) -> ServiceConfig:
        """新建一个配置版本。version 按 service 自增,新版自动成为 current。

        血缘:diff_from 指向创建时的当前生效版(新版由它派生而来);首版无前驱为 None。
        content_hash 落内容 SHA-256。
        """
        # 取该 service 当前最大版本号(无则 0),+1 作为新版本
        max_version = (
            await self._session.execute(
                select(func.max(ServiceConfig.version)).where(
                    ServiceConfig.service_id == service_id
                )
            )
        ).scalar_one_or_none() or 0

        # 血缘前驱:新版派生自创建时的当前生效版(在切换 current 之前取),首版为 None
        prev_current = await self.get_current(service_id)
        diff_from = prev_current.id if prev_current is not None else None

        # 旧 current 全部置 False(切换互斥)
        await self._session.execute(
            update(ServiceConfig)
            .where(
                ServiceConfig.service_id == service_id,
                ServiceConfig.is_current.is_(True),
            )
            .values(is_current=False)
        )

        config = ServiceConfig(
            service_id=service_id,
            version=max_version + 1,
            content=content,
            content_hash=self._hash_content(content),
            diff_from=diff_from,
            format=format,
            created_by=created_by,
            comment=comment,
            target_path=target_path,
            is_current=True,
        )
        self._session.add(config)
        await self._session.flush()
        return config

    async def get_version(self, service_id: str, version: int) -> ServiceConfig:
        stmt = select(ServiceConfig).where(
            ServiceConfig.service_id == service_id,
            ServiceConfig.version == version,
        )
        config = (await self._session.execute(stmt)).scalar_one_or_none()
        if config is None:
            raise AppError("config_not_found", "配置版本不存在", status_code=404)
        return config

    async def list_versions(self, service_id: str) -> Sequence[ServiceConfig]:
        """列出该 service 的全部配置版本,最新在前(供版本历史与 diff)。"""
        stmt = (
            select(ServiceConfig)
            .where(ServiceConfig.service_id == service_id)
            .order_by(ServiceConfig.version.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_current(self, service_id: str) -> ServiceConfig | None:
        """取当前生效配置版本;无配置返回 None。"""
        stmt = select(ServiceConfig).where(
            ServiceConfig.service_id == service_id,
            ServiceConfig.is_current.is_(True),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def activate(self, service_id: str, version: int) -> ServiceConfig:
        """切换 current 到指定历史版本(配置回滚)。目标版不存在抛 404。"""
        target = await self.get_version(service_id, version)
        await self._session.execute(
            update(ServiceConfig)
            .where(
                ServiceConfig.service_id == service_id,
                ServiceConfig.is_current.is_(True),
            )
            .values(is_current=False)
        )
        target.is_current = True
        await self._session.flush()
        return target
