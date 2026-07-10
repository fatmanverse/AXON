"""T0.5 凭证保险箱:LocalSecretStore(Fernet)存取/轮转,业务只见 credential_id。"""

import pytest

from app.core.secrets import (
    LocalSecretStore,
    SecretNotFound,
    generate_master_key,
)


@pytest.fixture
def store() -> LocalSecretStore:
    return LocalSecretStore(master_key=generate_master_key())


def test_put_returns_opaque_credential_id(store):
    cid = store.put("ssh-key", "-----BEGIN PRIVATE KEY-----\nabc\n")
    assert isinstance(cid, str)
    assert cid
    # id 不得等于明文(不能泄漏)
    assert "BEGIN PRIVATE KEY" not in cid


def test_get_roundtrips_plaintext(store):
    secret = "s3cr3t-token"
    cid = store.put("gitlab-token", secret)
    assert store.get(cid) == secret


def test_stored_ciphertext_is_not_plaintext(store):
    secret = "hunter2"
    cid = store.put("pw", secret)
    # 内部密文不得含明文(§13 不落明文)
    raw = store._blobs[cid]  # noqa: SLF001 白盒断言:确认加密而非明文
    assert secret.encode() not in raw


def test_get_missing_raises(store):
    with pytest.raises(SecretNotFound):
        store.get("nonexistent-id")


def test_rotate_changes_ciphertext_keeps_id_and_plaintext(store):
    cid = store.put("token", "v1")
    before = store._blobs[cid]  # noqa: SLF001
    store.rotate(cid, "v2")
    after = store._blobs[cid]  # noqa: SLF001
    assert store.get(cid) == "v2"
    assert before != after  # 密文变化
    # 轮转不改变引用 id,调用方无需更新外键
    assert store.get(cid) == "v2"


def test_rotate_missing_raises(store):
    with pytest.raises(SecretNotFound):
        store.rotate("nope", "x")


def test_different_master_key_cannot_decrypt(store):
    cid = store.put("k", "data")
    blob = store._blobs[cid]  # noqa: SLF001
    other = LocalSecretStore(master_key=generate_master_key())
    other._blobs[cid] = blob  # noqa: SLF001 模拟换主密钥后读旧密文
    with pytest.raises(Exception):  # noqa: B017 解密失败(密钥不匹配)
        other.get(cid)
