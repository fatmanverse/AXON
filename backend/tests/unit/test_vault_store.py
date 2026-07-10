"""T0.5 VaultSecretStore:用假 hvac client 验证接口契约(不连真 Vault)。"""

import pytest

from app.core.secrets import SecretNotFound, SecretStore, VaultSecretStore


class _FakeKvV2:
    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    def create_or_update_secret(self, path, secret, mount_point):
        # KV v2 语义:整体覆盖当前版本
        self._store[path] = {"data": {"data": dict(secret)}}

    def read_secret_version(self, path, mount_point):
        if path not in self._store:
            raise KeyError(path)  # 模拟 hvac InvalidPath
        return self._store[path]


class _FakeClient:
    def __init__(self) -> None:
        backing: dict[str, dict] = {}

        class _Kv:
            v2 = _FakeKvV2(backing)

        class _Secrets:
            kv = _Kv()

        self.secrets = _Secrets()


@pytest.fixture
def vault() -> VaultSecretStore:
    return VaultSecretStore(_FakeClient())


def test_vault_conforms_to_protocol(vault):
    assert isinstance(vault, SecretStore)


def test_vault_put_get_roundtrip(vault):
    cid = vault.put("gitlab-token", "glpat-xxx")
    assert vault.get(cid) == "glpat-xxx"


def test_vault_get_missing_raises(vault):
    with pytest.raises(SecretNotFound):
        vault.get("missing")


def test_vault_rotate(vault):
    cid = vault.put("t", "v1")
    vault.rotate(cid, "v2")
    assert vault.get(cid) == "v2"


def test_vault_rotate_missing_raises(vault):
    with pytest.raises(SecretNotFound):
        vault.rotate("nope", "x")
