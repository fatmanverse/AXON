"""T0.12 限流:令牌桶算法单测(纯逻辑,不依赖 HTTP)。"""

import pytest

from app.core.ratelimit import TokenBucket


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, tuple[object, ...]]] = []

    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        self.calls.append((script, numkeys, args))
        return 1


class _UnavailableRedis:
    async def eval(self, *_args: object) -> int:
        from redis.exceptions import ConnectionError

        raise ConnectionError("down")


def test_bucket_allows_up_to_capacity():
    bucket = TokenBucket(capacity=3, refill_per_sec=0, now=lambda: 0.0)
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False  # 第 4 个被拒


def test_bucket_refills_over_time():
    clock = {"t": 0.0}
    bucket = TokenBucket(capacity=2, refill_per_sec=1.0, now=lambda: clock["t"])
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False
    # 过 1 秒补 1 个令牌
    clock["t"] = 1.0
    assert bucket.take() is True
    assert bucket.take() is False


def test_bucket_does_not_overfill():
    clock = {"t": 0.0}
    bucket = TokenBucket(capacity=2, refill_per_sec=10.0, now=lambda: clock["t"])
    bucket.take()
    bucket.take()
    # 过很久,也只补到 capacity 上限
    clock["t"] = 100.0
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False


def test_registry_isolates_clients():
    from app.core.ratelimit import RateLimiter

    clock = {"t": 0.0}
    limiter = RateLimiter(capacity=1, refill_per_sec=0, now=lambda: clock["t"])
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False  # a 用完
    assert limiter.allow("client-b") is True  # b 独立桶


async def test_redis_limiter_uses_atomic_namespaced_bucket():
    from app.core.ratelimit import RedisRateLimiter

    redis = _FakeRedis()
    limiter = RedisRateLimiter(
        redis,
        capacity=10,
        refill_per_sec=2.0,
        namespace="axon:test",
    )

    assert await limiter.allow("client-a") is True
    assert len(redis.calls) == 1
    script, numkeys, args = redis.calls[0]
    assert "redis.call('TIME')" in script
    assert numkeys == 1
    assert args[0] == "axon:test:client-a"


async def test_redis_limiter_fails_explicitly_when_backend_is_down():
    from app.core.ratelimit import RateLimitUnavailable, RedisRateLimiter

    limiter = RedisRateLimiter(_UnavailableRedis(), capacity=1, refill_per_sec=1)

    with pytest.raises(RateLimitUnavailable):
        await limiter.allow("client-a")
