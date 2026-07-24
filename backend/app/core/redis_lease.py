"""Redis token lease for singleton worker jobs."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from redis.exceptions import RedisError


class RedisLease:
    _RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
    _RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, redis: Any, key: str, *, ttl_sec: float) -> None:
        if ttl_sec <= 0:
            raise ValueError("Redis lease ttl must be positive")
        self._redis = redis
        self._key = key
        self._ttl_ms = max(1000, int(ttl_sec * 1000))
        self._token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lost = False

    @property
    def lost(self) -> bool:
        return self._lost

    def acquire(self) -> bool:
        try:
            acquired = self._redis.set(
                self._key,
                self._token,
                nx=True,
                px=self._ttl_ms,
            )
        except RedisError:
            raise
        if not acquired:
            return False
        self._thread = threading.Thread(target=self._renew_loop, daemon=True)
        self._thread.start()
        return True

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, self._ttl_ms / 1000 / 2))
        try:
            self._redis.eval(self._RELEASE, 1, self._key, self._token)
        except RedisError:
            # 释放失败不应覆盖任务结果；TTL 会自动回收租约。
            pass

    def _renew_loop(self) -> None:
        interval = max(0.2, self._ttl_ms / 1000 / 3)
        while not self._stop.wait(interval):
            try:
                renewed = self._redis.eval(
                    self._RENEW,
                    1,
                    self._key,
                    self._token,
                    str(self._ttl_ms),
                )
            except RedisError:
                self._lost = True
                return
            if not renewed:
                self._lost = True
                return

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()
