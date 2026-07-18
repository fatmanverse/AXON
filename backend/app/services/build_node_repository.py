"""build_nodes 数据访问层(构建能力一期,§方案 A)。

一期只跑控制面本地节点:ensure_local_node 幂等取/建名为 "control-plane" 的本地
节点(server_id=None)。create/list/delete/get 支撑后续注册更多节点的架构预留。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.build_node import BuildNode, BuildNodeStatus

# 控制面本地节点的固定名字:一期唯一的构建执行者(方案 A「1 号构建节点」)。
LOCAL_NODE_NAME = "control-plane"


class BuildNodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_local_node(self) -> BuildNode:
        """幂等取/建控制面本地构建节点。已存在返回既有,不重复建。"""
        existing = await self._find_by_name(LOCAL_NODE_NAME)
        if existing is not None:
            return existing
        node = BuildNode(
            name=LOCAL_NODE_NAME,
            server_id=None,
            status=BuildNodeStatus.ONLINE,
            labels={"local": True},
            max_concurrent=1,
        )
        self._session.add(node)
        await self._session.flush()
        return node

    async def get(self, node_id: str) -> BuildNode:
        node = await self._session.get(BuildNode, node_id)
        if node is None:
            raise AppError("build_node_not_found", "构建节点不存在", status_code=404)
        return node

    async def list(self) -> Sequence[BuildNode]:
        result = await self._session.execute(select(BuildNode).order_by(BuildNode.name))
        return result.scalars().all()

    async def create(
        self,
        *,
        name: str,
        server_id: str | None = None,
        host: str | None = None,
        ssh_credential_id: str | None = None,
        labels: dict | None = None,
        max_concurrent: int = 1,
    ) -> BuildNode:
        node = BuildNode(
            name=name,
            server_id=server_id,
            host=host,
            ssh_credential_id=ssh_credential_id,
            status=BuildNodeStatus.UNKNOWN,
            labels=labels or {},
            max_concurrent=max_concurrent,
        )
        self._session.add(node)
        await self._session.flush()
        return node

    async def delete(self, node_id: str) -> None:
        node = await self.get(node_id)
        await self._session.delete(node)
        await self._session.flush()

    async def _find_by_name(self, name: str) -> BuildNode | None:
        stmt = select(BuildNode).where(BuildNode.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
