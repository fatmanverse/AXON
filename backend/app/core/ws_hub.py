"""实时推送 Hub:进程内主题订阅/发布(§2/§3 "UI 轮询/推送")。

设计要点:
- 主题(topic)维度组织订阅者,发布只投递给匹配主题的订阅者。
- 每个订阅者持有一个有界队列;满队列时**丢弃最新消息**做背压保护,
  慢订阅者不拖垮快订阅者(实时推送可容忍偶发丢帧,由前端轮询兜底补齐)。
- 接口保持进程内实现,后续多实例可换成 Redis pub/sub 扇出而不改调用方。

用途:任务进度(task:<id>)、部署 feed(deployments)、告警(alerts)等主题。
"""

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

from redis.exceptions import RedisError

Message = dict[str, Any]

# 订阅句柄即有界队列;取此别名让调用方(WS 端点)语义清晰,
# 后续换 Redis pub/sub 时只需保持 get/empty/qsize 语义即可。
Subscription = asyncio.Queue

DEFAULT_MAX_QUEUE = 100


class Hub:
    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE) -> None:
        self._max_queue = max_queue
        self._topics: dict[str, set[asyncio.Queue[Message]]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue[Message]:
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=self._max_queue)
        self._topics[topic].add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue[Message]) -> None:
        subs = self._topics.get(topic)
        if subs is None:
            return
        subs.discard(queue)
        if not subs:
            self._topics.pop(topic, None)

    async def publish(self, topic: str, message: Message) -> None:
        for queue in list(self._topics.get(topic, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # 背压:丢弃该慢订阅者的这条消息,不阻塞其他订阅者
                pass

    def topic_count(self) -> int:
        return len(self._topics)


class RedisHub(Hub):
    """Redis pub/sub 扇出 Hub；本地订阅仍使用有界 asyncio.Queue。"""

    def __init__(
        self,
        redis: Any,
        max_queue: int = DEFAULT_MAX_QUEUE,
        *,
        namespace: str = "axon:ws",
    ) -> None:
        super().__init__(max_queue=max_queue)
        self._redis = redis
        self._channel = f"{namespace.rstrip(':')}:events"
        self._publisher_id = uuid.uuid4().hex
        self._pubsub: Any | None = None
        self._listener: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._listener is not None:
            return
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._listener = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None
        if self._pubsub is not None:
            await self._pubsub.close()
            self._pubsub = None

    async def publish(self, topic: str, message: Message) -> None:
        self._publish_local(topic, message)
        envelope = json.dumps(
            {"publisher": self._publisher_id, "topic": topic, "message": message},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self._redis.publish(self._channel, envelope)
        except RedisError as exc:
            raise RuntimeError("Redis WebSocket 扇出不可用") from exc

    def _publish_local(self, topic: str, message: Message) -> None:
        for queue in list(self._topics.get(topic, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def _listen(self) -> None:
        assert self._pubsub is not None
        async for item in self._pubsub.listen():
            if item.get("type") not in {"message", "pmessage"}:
                continue
            raw = item.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                envelope = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if envelope.get("publisher") == self._publisher_id:
                continue
            topic = envelope.get("topic")
            message = envelope.get("message")
            if isinstance(topic, str) and isinstance(message, dict):
                self._publish_local(topic, message)


class RedisPublishHub(Hub):
    """Celery worker 使用的同步 Redis publisher，避免 async client 跨事件循环。"""

    def __init__(self, redis: Any, *, namespace: str = "axon:ws") -> None:
        super().__init__()
        self._redis = redis
        self._channel = f"{namespace.rstrip(':')}:events"
        self._publisher_id = uuid.uuid4().hex

    async def publish(self, topic: str, message: Message) -> None:
        envelope = json.dumps(
            {"publisher": self._publisher_id, "topic": topic, "message": message},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await asyncio.to_thread(self._redis.publish, self._channel, envelope)
        except RedisError as exc:
            raise RuntimeError("Redis WebSocket 扇出不可用") from exc


# 进程内单例:被 worker 回调与 API 侧共享(多实例部署时换 Redis 扇出)
_hub: Hub = Hub()


def configure_hub(hub: Hub) -> None:
    global _hub
    _hub = hub


def get_hub() -> Hub:
    return _hub
