"""T2.4 webhook HMAC 验签(设计 §8.3 ①)。

验证 verify_signature 的安全语义:
- 用对应源 secret 对 (timestamp + body) 做 HMAC-SHA256,签名匹配才通过。
- 时间窗校验:超出 ±window 的 timestamp 判为重放,拒绝。
- 双 secret 并存:轮转期任一 secret 验签通过。
- 篡改 body / 错 secret / 缺签名 → 拒绝。
- 用 compare_digest 防时序攻击(行为上等价,这里验证结果正确性)。
"""

import hashlib
import hmac
import time

import pytest

from app.core.webhook_signature import (
    SignatureError,
    build_signature,
    verify_signature,
)

_SECRET = "src-secret-abc"
_BODY = b'{"service":"billing","env":"prod","status":"success"}'


def _sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


def test_valid_signature_passes():
    ts = int(time.time())
    sig = _sign(_SECRET, ts, _BODY)
    # 不抛即通过
    verify_signature(body=_BODY, timestamp=ts, signature=sig, secrets=[_SECRET])


def test_build_signature_roundtrip():
    ts = int(time.time())
    sig = build_signature(secret=_SECRET, timestamp=ts, body=_BODY)
    verify_signature(body=_BODY, timestamp=ts, signature=sig, secrets=[_SECRET])


def test_tampered_body_rejected():
    ts = int(time.time())
    sig = _sign(_SECRET, ts, _BODY)
    with pytest.raises(SignatureError):
        verify_signature(
            body=_BODY + b"x", timestamp=ts, signature=sig, secrets=[_SECRET]
        )


def test_wrong_secret_rejected():
    ts = int(time.time())
    sig = _sign("other-secret", ts, _BODY)
    with pytest.raises(SignatureError):
        verify_signature(body=_BODY, timestamp=ts, signature=sig, secrets=[_SECRET])


def test_replay_outside_window_rejected():
    old = int(time.time()) - 600  # 10 分钟前,超出默认 ±300s 窗
    sig = _sign(_SECRET, old, _BODY)
    with pytest.raises(SignatureError, match="时间窗"):
        verify_signature(
            body=_BODY, timestamp=old, signature=sig, secrets=[_SECRET], window=300
        )


def test_future_timestamp_outside_window_rejected():
    future = int(time.time()) + 600
    sig = _sign(_SECRET, future, _BODY)
    with pytest.raises(SignatureError, match="时间窗"):
        verify_signature(
            body=_BODY, timestamp=future, signature=sig, secrets=[_SECRET], window=300
        )


def test_dual_secret_rotation_either_passes():
    ts = int(time.time())
    # 用旧 secret 签名,新旧并存时应通过
    sig_old = _sign("old-secret", ts, _BODY)
    verify_signature(
        body=_BODY,
        timestamp=ts,
        signature=sig_old,
        secrets=["new-secret", "old-secret"],
    )
    # 用新 secret 签名也通过
    sig_new = _sign("new-secret", ts, _BODY)
    verify_signature(
        body=_BODY,
        timestamp=ts,
        signature=sig_new,
        secrets=["new-secret", "old-secret"],
    )


def test_empty_secrets_rejected():
    ts = int(time.time())
    sig = _sign(_SECRET, ts, _BODY)
    with pytest.raises(SignatureError):
        verify_signature(body=_BODY, timestamp=ts, signature=sig, secrets=[])
