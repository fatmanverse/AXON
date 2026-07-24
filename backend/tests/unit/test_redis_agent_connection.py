"""Redis-backed Agent connection routing across API replicas."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import pytest

from app.services.agent_connection import (
    AgentMessage,
    AgentMessageKind,
    AgentRoutingError,
    ServerCommand,
)
from app.services.redis_agent_connection import RedisAgentConnectionManager


class _FakeRedisBus:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)


class _FakePubSub:
    def __init__(self, bus: _FakeRedisBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._channels: set[str] = set()

    async def subscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.add(channel)
            self._bus.subscribers[channel].add(self._queue)

    async def listen(self):
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        for channel in self._channels:
            self._bus.subscribers[channel].discard(self._queue)


class _FakeRedis:
    def __init__(self, bus: _FakeRedisBus) -> None:
        self._bus = bus

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._bus)

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        del ex
        self._bus.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._bus.values.get(key)

    async def publish(self, channel: str, message: str) -> int:
        queues = list(self._bus.subscribers[channel])
        for queue in queues:
            await queue.put({"type": "message", "channel": channel, "data": message})
        return len(queues)

    async def eval(self, script: str, numkeys: int, key: str, *args: str) -> int:
        del numkeys
        owner = args[0]
        if self._bus.values.get(key) != owner:
            return 0
        if "expire" in script:
            return 1
        self._bus.values.pop(key, None)
        return 1


class _RecordingTransport:
    def __init__(self) -> None:
        self.commands: asyncio.Queue[ServerCommand] = asyncio.Queue()

    async def send(self, command: ServerCommand) -> None:
        await self.commands.put(command)


async def test_routes_command_and_result_between_api_instances() -> None:
    bus = _FakeRedisBus()
    instance_a = RedisAgentConnectionManager(
        _FakeRedis(bus), instance_id="api-a", heartbeat_timeout=30.0
    )
    instance_b = RedisAgentConnectionManager(
        _FakeRedis(bus), instance_id="api-b", heartbeat_timeout=30.0
    )
    await instance_a.start()
    await instance_b.start()
    try:
        transport = _RecordingTransport()
        await instance_b.register_connection("agent-1", transport, now=0.0)

        received: asyncio.Queue[AgentMessage] = asyncio.Queue()
        instance_a.on_message(received.put_nowait)
        command = ServerCommand(task_id="task-1", action="exec", params={"command": "uptime"})
        await instance_a.send_command("agent-1", command)

        assert await asyncio.wait_for(transport.commands.get(), timeout=1.0) == command

        result = AgentMessage(
            agent_id="agent-1",
            kind=AgentMessageKind.RESULT,
            task_id="task-1",
            ok=True,
            detail="done",
        )
        await instance_b.handle_inbound(result)
        assert await asyncio.wait_for(received.get(), timeout=1.0) == result
    finally:
        await instance_b.unregister_connection("agent-1")
        await instance_a.stop()
        await instance_b.stop()


async def test_redis_owner_is_canonical_when_stale_local_connection_remains() -> None:
    bus = _FakeRedisBus()
    instance_a = RedisAgentConnectionManager(
        _FakeRedis(bus), instance_id="api-a", heartbeat_timeout=30.0
    )
    instance_b = RedisAgentConnectionManager(
        _FakeRedis(bus), instance_id="api-b", heartbeat_timeout=30.0
    )
    await instance_a.start()
    await instance_b.start()
    try:
        stale_transport = _RecordingTransport()
        current_transport = _RecordingTransport()
        await instance_a.register_connection("agent-1", stale_transport, now=0.0)
        await instance_b.register_connection("agent-1", current_transport, now=1.0)
        await instance_a.heartbeat_connection("agent-1", now=2.0)

        command = ServerCommand(task_id="task-2", action="status")
        await instance_a.send_command("agent-1", command)

        assert await asyncio.wait_for(current_transport.commands.get(), timeout=1.0) == command
        assert stale_transport.commands.empty()
    finally:
        await instance_a.stop()
        await instance_b.stop()


async def test_missing_redis_owner_reports_agent_offline() -> None:
    manager = RedisAgentConnectionManager(_FakeRedis(_FakeRedisBus()), instance_id="api-a")
    await manager.start()
    try:
        with pytest.raises(KeyError, match="无活跃连接"):
            await manager.send_command("missing", ServerCommand(task_id="task-3", action="status"))
    finally:
        await manager.stop()


async def test_stale_owner_without_subscriber_fails_immediately() -> None:
    bus = _FakeRedisBus()
    bus.values["axon:agent:owner:agent-1"] = "dead-api"
    manager = RedisAgentConnectionManager(_FakeRedis(bus), instance_id="api-a")
    await manager.start()
    try:
        with pytest.raises(AgentRoutingError, match="owner 实例不可用"):
            await manager.send_command("agent-1", ServerCommand(task_id="task-4", action="status"))
    finally:
        await manager.stop()
