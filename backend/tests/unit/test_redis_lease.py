from redis.exceptions import ConnectionError

from app.core.redis_lease import RedisLease


class _FakeRedis:
    def __init__(self) -> None:
        self.value = None
        self.eval_calls: list[tuple[str, tuple[object, ...]]] = []

    def set(self, key, value, *, nx, px):
        if self.value is not None:
            return False
        self.value = value
        return True

    def eval(self, script, numkeys, *args):
        self.eval_calls.append((script, args))
        if "PEXPIRE" in script and self.value == args[1]:
            return 1
        if "DEL" in script and self.value == args[1]:
            self.value = None
            return 1
        return 0


def test_redis_lease_acquires_and_releases_only_its_token():
    redis = _FakeRedis()
    lease = RedisLease(redis, "lease", ttl_sec=1)

    assert lease.acquire() is True
    lease.release()

    assert redis.value is None
    assert redis.eval_calls


def test_redis_lease_reports_contention_without_overwriting_owner():
    redis = _FakeRedis()
    first = RedisLease(redis, "lease", ttl_sec=1)
    second = RedisLease(redis, "lease", ttl_sec=1)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()


def test_redis_lease_propagates_backend_errors():
    class DownRedis:
        def set(self, *_args, **_kwargs):
            raise ConnectionError("down")

    lease = RedisLease(DownRedis(), "lease", ttl_sec=1)

    try:
        lease.acquire()
    except ConnectionError:
        pass
    else:
        raise AssertionError("backend failure must not silently run without lease")
