"""令牌桶限流(T0.12)。

MVP 用进程内令牌桶,按客户端 key(默认取来源 IP)隔离。
接口刻意保持简单,后续需要跨实例分布式限流时,可替换 RateLimiter
的实现为 Redis 版而不改中间件调用方。
"""

import threading
import time
from collections.abc import Callable


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
