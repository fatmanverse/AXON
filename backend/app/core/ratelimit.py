"""令牌桶限流(T0.12)。

MVP 用进程内令牌桶,按客户端 key(默认取来源 IP)隔离。
接口刻意保持简单,后续需要跨实例分布式限流时,可替换 RateLimiter
的实现为 Redis 版而不改中间件调用方。
"""

import threading
import time
from collections.abc import Callable
from typing import Any

from redis.exceptions import RedisError


class RateLimitUnavailable(RuntimeError):
    """分布式限流状态不可用；调用方必须显式失败，不能退回进程内桶。"""


class TokenBucket:
    """单个令牌桶:容量 capacity,每秒补 refill_per_sec 个令牌。

    now 可注入,便于测试用假时钟。线程安全由外层 RateLimiter 的锁保证。
    """

    __slots__ = ("_capacity", "_refill", "_now", "_tokens", "_last")

    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill = refill_per_sec
        self._now = now
        self._tokens = float(capacity)
        self._last = now()

    def _replenish(self) -> None:
        if self._refill <= 0:
            return
        current = self._now()
        elapsed = current - self._last
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        self._last = current

    def take(self, cost: float = 1.0) -> bool:
        self._replenish()
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False


class RateLimiter:
    """按 key 维护独立令牌桶的注册表。"""

    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill = refill_per_sec
        self._now = now
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self._capacity, self._refill, self._now)
                self._buckets[key] = bucket
            return bucket.take(cost)


class RedisRateLimiter:
    """基于 Redis Lua 原子令牌桶，供多副本 API 共享限流状态。"""

    _SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local values = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(values[1])
local last = tonumber(values[2])
if tokens == nil then
  tokens = capacity
  last = now
else
  tokens = math.min(capacity, tokens + math.max(0, now - last) * refill)
end
local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'last', now)
redis.call('PEXPIRE', key, ttl)
return allowed
"""

    def __init__(
        self,
        redis: Any,
        capacity: int,
        refill_per_sec: float,
        *,
        namespace: str = "axon:ratelimit",
    ) -> None:
        if capacity <= 0 or refill_per_sec < 0:
            raise ValueError("Redis 限流 capacity 必须为正，refill_per_sec 不能为负")
        self._redis = redis
        self._capacity = capacity
        self._refill = refill_per_sec
        self._namespace = namespace.rstrip(":")
        refill_window = capacity / refill_per_sec if refill_per_sec > 0 else 3600
        self._ttl_ms = max(60_000, int(refill_window * 2 * 1000))

    async def allow(self, key: str, cost: float = 1.0) -> bool:
        try:
            result = await self._redis.eval(
                self._SCRIPT,
                1,
                f"{self._namespace}:{key}",
                str(self._capacity),
                str(self._refill),
                str(cost),
                str(self._ttl_ms),
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Redis 限流状态不可用") from exc
        return bool(int(result))
