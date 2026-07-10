"""T0.4 密码哈希与 JWT 签发/校验。"""

import time

import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

_SECRET = "test-secret-key-please-change"


def test_password_hash_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"  # 不是明文
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token(
        subject="alice", secret=_SECRET, roles=["admin"], expires_minutes=60
    )
    claims = decode_access_token(token, secret=_SECRET)
    assert claims.subject == "alice"
    assert claims.roles == ["admin"]


def test_jwt_rejects_wrong_secret():
    token = create_access_token(subject="a", secret=_SECRET, roles=[])
    with pytest.raises(Exception):  # noqa: B017 InvalidSignature
        decode_access_token(token, secret="other-secret")


def test_jwt_rejects_expired():
    token = create_access_token(
        subject="a", secret=_SECRET, roles=[], expires_minutes=-1  # 已过期
    )
    time.sleep(0.01)
    with pytest.raises(Exception):  # noqa: B017 ExpiredSignatureError
        decode_access_token(token, secret=_SECRET)
