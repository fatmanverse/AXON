"""入向 webhook 的 HMAC 签名验签(T2.4,设计 §8.3 ①)。

所有入向 webhook(deployment / scan / alert)统一用本模块验签,替代脆弱的共享
Bearer token:

- 每个 webhook 源分配独立 secret,对 ``{timestamp}.{body}`` 做 HMAC-SHA256,
  签名放 X-Signature header,服务端用对应源的 secret 验签。
- 签名载荷带 timestamp,校验 ±window(默认 5min)防重放。
- 双 secret 并存:轮转过渡期任一 secret 验签通过,平滑换密钥。
- 用 hmac.compare_digest 常量时间比较,防时序侧信道。

本模块只做纯粹的签名/时间校验,不碰 DB/HTTP,便于单测与复用。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Sequence

DEFAULT_WINDOW_SECONDS = 300


class SignatureError(Exception):
    """验签失败:签名不匹配、时间窗超限或缺少可用 secret。"""


def build_signature(*, secret: str, timestamp: int, body: bytes) -> str:
    """按约定用一个 secret 生成签名(供上报方/测试构造)。

    载荷是 ``{timestamp}.`` 前缀拼原始 body 字节——把时间戳纳入签名,篡改
    时间戳即令签名失效,配合时间窗校验防重放。
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


def verify_signature(
    *,
    body: bytes,
    timestamp: int,
    signature: str,
    secrets: Sequence[str],
    window: int = DEFAULT_WINDOW_SECONDS,
    now: int | None = None,
) -> None:
    """验签:时间窗内且任一 secret 匹配才通过,否则抛 SignatureError。

    先校验时间窗(便宜且能挡掉大量重放),再逐个 secret 用常量时间比较。
    secrets 支持传入新旧两把,支撑无缝轮转(§8.3 ①)。
    """
    current = now if now is not None else int(time.time())
    if abs(current - timestamp) > window:
        raise SignatureError(f"时间窗校验失败: 时间戳偏移超过 {window}s")

    if not secrets:
        raise SignatureError("未配置该来源的验签 secret")

    for secret in secrets:
        expected = build_signature(secret=secret, timestamp=timestamp, body=body)
        if hmac.compare_digest(expected, signature):
            return

    raise SignatureError("签名不匹配")
