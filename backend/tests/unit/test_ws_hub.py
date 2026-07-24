"""T0.10 实时推送:Hub 主题订阅/发布(纯异步逻辑,不依赖 HTTP)。"""

import asyncio

from app.core.ws_hub import Hub, RedisHub, RedisPublishHub


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class _FakeSyncRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


async def test_subscriber_receives_published_message():
    hub = Hub()
    sub = hub.subscribe("task:abc")
    await hub.publish("task:abc", {"status": "running"})
    msg = await asyncio.wait_for(sub.get(), timeout=1)
    assert msg == {"status": "running"}
    hub.unsubscribe("task:abc", sub)


async def test_message_only_goes_to_matching_topic():
    hub = Hub()
    a = hub.subscribe("task:a")
    b = hub.subscribe("task:b")
    await hub.publish("task:a", {"n": 1})
    got_a = await asyncio.wait_for(a.get(), timeout=1)
    assert got_a == {"n": 1}
    assert b.empty()  # b 主题没收到


async def test_multiple_subscribers_same_topic_all_receive():
    hub = Hub()
    s1 = hub.subscribe("deployments")
    s2 = hub.subscribe("deployments")
    await hub.publish("deployments", {"svc": "order"})
    assert await asyncio.wait_for(s1.get(), timeout=1) == {"svc": "order"}
    assert await asyncio.wait_for(s2.get(), timeout=1) == {"svc": "order"}


async def test_unsubscribe_stops_delivery():
    hub = Hub()
    sub = hub.subscribe("alerts")
    hub.unsubscribe("alerts", sub)
    await hub.publish("alerts", {"sev": "critical"})
    assert sub.empty()


async def test_publish_to_empty_topic_is_noop():
    hub = Hub()
    # 无订阅者,不应抛错
    await hub.publish("nobody", {"x": 1})


async def test_slow_subscriber_does_not_block_others():
    """满队列的慢订阅者被丢弃消息,不拖垮其他订阅者(背压保护)。

    max_queue=1:两者都收到 n=1(队列各满)。fast 取走后腾出空间,
    slow 一直不读保持满。再 publish n=2:fast 有空间收到,slow 已满被丢弃。
    关键不变量:publish 面对满队列既不阻塞也不抛错。
    """
    hub = Hub(max_queue=1)
    slow = hub.subscribe("t")
    fast = hub.subscribe("t")
    await hub.publish("t", {"n": 1})
    assert await asyncio.wait_for(fast.get(), timeout=1) == {"n": 1}  # fast 腾空
    await hub.publish("t", {"n": 2})  # slow 队列已满,第二条对 slow 丢弃
    assert await asyncio.wait_for(fast.get(), timeout=1) == {"n": 2}
    # slow 只留住第一条,不报错
    assert slow.qsize() == 1


async def test_redis_hub_publishes_cross_instance_envelope_and_local_delivery():
    redis = _FakeRedis()
    hub = RedisHub(redis, namespace="axon:test")
    queue = hub.subscribe("deployments")

    await hub.publish("deployments", {"id": "d-1"})

    assert await queue.get() == {"id": "d-1"}
    assert redis.published
    assert redis.published[0][0] == "axon:test:events"
    assert '"topic":"deployments"' in redis.published[0][1]


async def test_worker_publish_hub_uses_sync_redis_without_event_loop_binding():
    redis = _FakeSyncRedis()
    hub = RedisPublishHub(redis, namespace="axon:test")

    await hub.publish("alerts", {"id": "a-1"})

    assert redis.published[0][0] == "axon:test:events"
    assert '"topic":"alerts"' in redis.published[0][1]
