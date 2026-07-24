"""Build node capability selection and cross-process capacity leases."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

from redis.exceptions import RedisError

from app.core.errors import AppError
from app.models.build_node import BuildNode, BuildNodeStatus

_memory_slots: dict[str, asyncio.BoundedSemaphore] = {}


class AsyncBuildLease:
    _RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, redis: Any, key: str, ttl_sec: float) -> None:
        self._redis = redis
        self._key = key
        self._token = uuid.uuid4().hex
        self._ttl_ms = max(1000, int(ttl_sec * 1000))

    async def acquire(self) -> bool:
        try:
            return bool(await self._redis.set(self._key, self._token, nx=True, px=self._ttl_ms))
        except RedisError as exc:
            raise AppError(
                "build_coordination_unavailable",
                "构建节点调度 Redis 不可用",
                status_code=503,
            ) from exc

    async def release(self) -> None:
        try:
            await self._redis.eval(self._RELEASE, 1, self._key, self._token)
        except RedisError:
            # TTL 会自动回收；不覆盖构建本身的成功/失败结论。
            return


class BuildNodeSlot:
    def __init__(self, *, redis_lease: AsyncBuildLease | None = None, semaphore=None) -> None:
        self._redis_lease = redis_lease
        self._semaphore = semaphore

    async def release(self) -> None:
        if self._redis_lease is not None:
            await self._redis_lease.release()
        elif self._semaphore is not None:
            self._semaphore.release()


class BuildNodeScheduler:
    def __init__(self, redis: Any | None, *, lease_ttl_sec: float = 3600.0) -> None:
        self._redis = redis
        self._ttl = lease_ttl_sec

    async def acquire(
        self,
        nodes: list[BuildNode],
        *,
        required_labels: Mapping[str, Any] | None = None,
    ) -> tuple[BuildNode, BuildNodeSlot]:
        required = required_labels or {}
        for node in nodes:
            if node.status != BuildNodeStatus.ONLINE:
                continue
            labels = node.labels or {}
            if any(labels.get(key) != value for key, value in required.items()):
                continue
            slot = await self._try_slot(node)
            if slot is not None:
                return node, slot
        raise AppError(
            "build_capacity_unavailable",
            "没有满足工具链标签且仍有并发容量的构建节点",
            status_code=503,
        )

    async def _try_slot(self, node: BuildNode) -> BuildNodeSlot | None:
        if self._redis is not None:
            for index in range(node.max_concurrent):
                lease = AsyncBuildLease(
                    self._redis,
                    f"axon:build-node:{node.id}:slot:{index}",
                    self._ttl,
                )
                if await lease.acquire():
                    return BuildNodeSlot(redis_lease=lease)
            return None

        semaphore = _memory_slots.setdefault(node.id, asyncio.BoundedSemaphore(node.max_concurrent))
        if semaphore.locked():
            return None
        await semaphore.acquire()
        return BuildNodeSlot(semaphore=semaphore)
