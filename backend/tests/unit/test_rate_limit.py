"""T0.12 限流:令牌桶算法单测(纯逻辑,不依赖 HTTP)。"""

from app.core.ratelimit import TokenBucket


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
