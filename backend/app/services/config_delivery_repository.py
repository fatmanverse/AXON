"""config_deliveries 数据访问层(§14.5)。

一个配置版本下发到多个放置点,逐目标一条记录。create_pending 在编排开始时
批量建 pending;编排对每个目标执行后 mark_result 落 success/failed。查询按
config 聚合,供下发页逐目标展示部分成功/失败态。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.config_delivery import ConfigDelivery, DeliveryStatus


class ConfigDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self, *, config_id: str, placement_ids: Sequence[str]
    ) -> Sequence[ConfigDelivery]:
        """为一次下发批次逐目标建 pending 记录,返回建好的行(顺序同入参)。"""
        rows = [
            ConfigDelivery(config_id=config_id, placement_id=pid, status=DeliveryStatus.PENDING)
            for pid in placement_ids
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows

    async def mark_result(
        self,
        delivery_id: str,
        *,
        status: DeliveryStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> ConfigDelivery:
        """落单个目标的下发结果。记录不存在抛 404。"""
        delivery = await self._session.get(ConfigDelivery, delivery_id)
        if delivery is None:
            raise AppError("delivery_not_found", "下发记录不存在", status_code=404)
        delivery.status = status
        delivery.result = result
        delivery.error = error
        await self._session.flush()
        return delivery

    async def list_for_config(self, config_id: str) -> Sequence[ConfigDelivery]:
        """列出某配置版本的全部下发记录(最新在前),供逐目标结果展示。"""
        stmt = (
            select(ConfigDelivery)
            .where(ConfigDelivery.config_id == config_id)
            .order_by(ConfigDelivery.created_at.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()
