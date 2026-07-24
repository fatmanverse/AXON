"""Redis-backed Agent routing for horizontally scaled API replicas.

The local manager owns the gRPC transport. Redis owns Agent connection
ownership and relays commands/results between API processes, so an HTTP
request does not need to land on the same replica as the long-lived stream.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from redis.exceptions import RedisError

from app.services.agent_connection import (
    AgentConnectionManager,
    AgentMessage,
    AgentMessageKind,
    AgentRoutingError,
    CommandTransport,
    ServerCommand,
)


class RedisAgentConnectionManager(AgentConnectionManager):
    """Replicate Agent ownership and messages through Redis pub/sub."""

    def __init__(
        self,
        redis: Any,
        *,
        instance_id: str | None = None,
        heartbeat_timeout: float = 30.0,
        namespace: str = "axon:agent",
    ) -> None:
        super().__init__(heartbeat_timeout=heartbeat_timeout)
        self._redis = redis
        self._instance_id = instance_id or uuid4().hex
        self._namespace = namespace.rstrip(":")
        self._owner_ttl = max(1, int(heartbeat_timeout * 2))
        self._command_channel = f"{self._namespace}:commands:{self._instance_id}"
        self._message_channel = f"{self._namespace}:messages"
        self._pubsub: Any | None = None
        self._listener: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._listener is not None:
            return
        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self._command_channel, self._message_channel)
            self._listener = asyncio.create_task(self._listen())
        except RedisError as exc:
            self._pubsub = None
            raise AgentRoutingError("Redis Agent 路由不可用") from exc

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

    def _owner_key(self, agent_id: str) -> str:
        return f"{self._namespace}:owner:{agent_id}"

    async def register_connection(
        self, agent_id: str, transport: CommandTransport, *, now: float
    ) -> None:
        super().register(agent_id, transport, now=now)
        try:
            await self._redis.set(self._owner_key(agent_id), self._instance_id, ex=self._owner_ttl)
        except RedisError as exc:
            super().unregister(agent_id)
            raise AgentRoutingError("Redis Agent owner 登记不可用") from exc

    async def heartbeat_connection(self, agent_id: str, *, now: float) -> None:
        super().heartbeat(agent_id, now=now)
        if not self.is_online(agent_id, now=now):
            return
        try:
            refreshed = await self._redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                1,
                self._owner_key(agent_id),
                self._instance_id,
                str(self._owner_ttl),
            )
            if not refreshed:
                # A newer stream owns this agent. Retire the stale local
                # transport so it cannot send commands or reclaim ownership.
                super().unregister(agent_id)
        except RedisError as exc:
            raise AgentRoutingError("Redis Agent owner 心跳不可用") from exc

    async def unregister_connection(self, agent_id: str) -> None:
        super().unregister(agent_id)
        try:
            await self._redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1,
                self._owner_key(agent_id),
                self._instance_id,
            )
        except RedisError as exc:
            raise AgentRoutingError("Redis Agent owner 摘除不可用") from exc

    async def send_command(self, agent_id: str, command: ServerCommand) -> None:
        try:
            owner = await self._redis.get(self._owner_key(agent_id))
            if isinstance(owner, bytes):
                owner = owner.decode("utf-8")
            if not owner:
                raise KeyError(f"agent 无活跃连接: {agent_id}")
            if owner == self._instance_id:
                await super().send_command(agent_id, command)
                return
            subscribers = await self._redis.publish(
                f"{self._namespace}:commands:{owner}",
                json.dumps(
                    {
                        "agent_id": agent_id,
                        "task_id": command.task_id,
                        "action": command.action,
                        "params": command.params,
                        "fence": command.fence,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            if subscribers == 0:
                raise AgentRoutingError("Agent owner 实例不可用")
        except KeyError:
            raise
        except RedisError as exc:
            raise AgentRoutingError("Redis Agent 命令路由不可用") from exc

    async def handle_inbound(self, message: AgentMessage) -> None:
        await super().handle_inbound(message)
        try:
            await self._redis.publish(
                self._message_channel,
                json.dumps(
                    {
                        "publisher": self._instance_id,
                        "agent_id": message.agent_id,
                        "kind": message.kind.value,
                        "task_id": message.task_id,
                        "ok": message.ok,
                        "detail": message.detail,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        except RedisError as exc:
            raise AgentRoutingError("Redis Agent 结果广播不可用") from exc

    async def _listen(self) -> None:
        assert self._pubsub is not None
        async for item in self._pubsub.listen():
            if item.get("type") not in {"message", "pmessage"}:
                continue
            channel = item.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8")
            raw = item.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if channel == self._command_channel:
                await self._deliver_command(payload)
            elif channel == self._message_channel and payload.get("publisher") != self._instance_id:
                await self._deliver_message(payload)

    async def _deliver_command(self, payload: dict[str, Any]) -> None:
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str):
            return
        command = ServerCommand(
            task_id=str(payload.get("task_id", "")),
            action=str(payload.get("action", "")),
            params={str(k): str(v) for k, v in dict(payload.get("params") or {}).items()},
            fence=int(payload.get("fence", 0)),
        )
        try:
            await super().send_command(agent_id, command)
        except KeyError:
            # Owner TTL and stream lifetime can race. The originating gateway
            # will time out and leave the operation unknown, never successful.
            return

    async def _deliver_message(self, payload: dict[str, Any]) -> None:
        try:
            message = AgentMessage(
                agent_id=str(payload["agent_id"]),
                kind=AgentMessageKind(str(payload["kind"])),
                task_id=payload.get("task_id"),
                ok=payload.get("ok"),
                detail=str(payload.get("detail", "")),
            )
        except (KeyError, ValueError, TypeError):
            return
        await super().handle_inbound(message)
