"""servers 数据访问层(T1.1)。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.server import Server
from app.schemas.server import ServerCreate


class ServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, payload: ServerCreate) -> Server:
        server = Server(**payload.model_dump())
        self._session.add(server)
        await self._session.flush()
        return server

    async def get(self, server_id: str) -> Server:
        server = await self._session.get(Server, server_id)
        if server is None:
            raise AppError("server_not_found", "服务器不存在", status_code=404)
        return server

    async def list(self) -> Sequence[Server]:
        result = await self._session.execute(select(Server).order_by(Server.name))
        return result.scalars().all()

    async def update_labels(self, server_id: str, labels: dict[str, object]) -> Server:
        server = await self.get(server_id)
        server.labels = labels
        await self._session.flush()
        return server

    async def delete(self, server_id: str) -> None:
        server = await self.get(server_id)
        await self._session.delete(server)
        await self._session.flush()
