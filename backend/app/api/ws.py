"""WebSocket 实时推送端点(T0.10)。

协议:
- 连接时用 `?token=<JWT>` 鉴权(浏览器 WebSocket 无法可靠带 Authorization 头,用 query 传)。
- 客户端消息:{action: subscribe|unsubscribe, topic} / {action: ping}。
- 服务端消息:{type: subscribed|unsubscribed|pong, topic?} 及 Hub 推送的业务消息。

设计要点:
- 每连接跑两条协程——读客户端指令、把订阅主题的消息推给客户端——用 asyncio 并发。
- 鉴权失败按 WS 规范用 policy-violation(1008)关闭,而非 HTTP 401。
- Hub 为进程内实现,多实例部署时可替换为 Redis pub/sub(接口不变,见 ws_hub)。
"""

import asyncio

from fastapi import APIRouter, WebSocket
from jwt import InvalidTokenError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.core.ws_hub import Subscription, get_hub

router = APIRouter()

WS_POLICY_VIOLATION = 1008


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = getattr(websocket.app.state, "settings", None) or get_settings()
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return
    try:
        decode_access_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except InvalidTokenError:
        await websocket.close(code=WS_POLICY_VIOLATION)
        return

    await websocket.accept()
    hub = get_hub()
    # topic -> Subscription,记录本连接订阅,断开时统一清理
    subs: dict[str, Subscription] = {}
    pump_tasks: dict[str, asyncio.Task] = {}

    async def _pump(topic: str, sub: Subscription) -> None:
        """把某主题的消息持续推给客户端。"""
        try:
            while True:
                message = await sub.get()
                if websocket.application_state != WebSocketState.CONNECTED:
                    break
                await websocket.send_json({"type": "message", "topic": topic, "data": message})
        except (WebSocketDisconnect, RuntimeError):
            pass

    def _add_subscription(topic: str) -> None:
        if topic in subs:
            return
        sub = hub.subscribe(topic)
        subs[topic] = sub
        pump_tasks[topic] = asyncio.create_task(_pump(topic, sub))

    async def _remove_subscription(topic: str) -> None:
        sub = subs.pop(topic, None)
        if sub is None:
            return
        hub.unsubscribe(topic, sub)
        task = pump_tasks.pop(topic, None)
        if task is not None:
            task.cancel()

    try:
        while True:
            command = await websocket.receive_json()
            action = command.get("action")
            if action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action == "subscribe":
                topic = command.get("topic")
                if topic:
                    _add_subscription(topic)
                    await websocket.send_json({"type": "subscribed", "topic": topic})
            elif action == "unsubscribe":
                topic = command.get("topic")
                if topic:
                    await _remove_subscription(topic)
                    await websocket.send_json({"type": "unsubscribed", "topic": topic})
            else:
                await websocket.send_json(
                    {"type": "error", "message": f"未知指令: {action}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        for topic in list(subs.keys()):
            await _remove_subscription(topic)
