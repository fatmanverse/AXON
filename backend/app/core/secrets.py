"""凭证保险箱(§13)。

原则:SSH 私钥、平台 token 等敏感值一律进保险箱,业务表只存 credential_id,
严禁明文落库。SecretStore 抽象两实现:
- LocalSecretStore:Fernet 对称加密,主密钥来自外部(MVP / 无 Vault 环境)。
- VaultSecretStore:委托 HashiCorp Vault(生产)。
切换实现不改调用方(§2 Adapter 屏蔽差异)。
"""

import uuid
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet


class SecretNotFound(Exception):
    """按 credential_id 找不到凭证。"""


@runtime_checkable
class SecretStore(Protocol):
    """凭证读写接口。凭证以不透明 credential_id 引用。"""

    def put(self, name: str, plaintext: str) -> str:
        """存入凭证,返回 credential_id(供业务表外键引用)。"""
        ...

    def get(self, credential_id: str) -> str:
        """按 id 取回明文;不存在抛 SecretNotFound。"""
        ...

    def rotate(self, credential_id: str, new_plaintext: str) -> None:
        """轮转:更新密文但保持 id 不变,调用方外键无需变更。"""
        ...


def generate_master_key() -> str:
    """生成 Fernet 主密钥(base64)。生产由 KMS 托管、按环境注入。"""
    return Fernet.generate_key().decode()


def build_secret_store(settings) -> "SecretStore":
    """按配置构造保险箱实现。切换后端不改调用方。"""
    if settings.secret_backend == "vault":
        import hvac

        client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
        return VaultSecretStore(client)

    master_key = settings.secret_master_key or generate_master_key()
    return LocalSecretStore(master_key=master_key)


class LocalSecretStore:
    """Fernet 加密的本地实现。

    密文默认存内存(_blobs);生产可将 _blobs 换成 DB/文件后端,
    但主密钥务必来自外部 KMS,不与密文同处存放。
    """

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(master_key.encode())
        self._blobs: dict[str, bytes] = {}
        self._names: dict[str, str] = {}

    def put(self, name: str, plaintext: str) -> str:
        credential_id = uuid.uuid4().hex
        self._blobs[credential_id] = self._fernet.encrypt(plaintext.encode())
        self._names[credential_id] = name
        return credential_id

    def get(self, credential_id: str) -> str:
        blob = self._blobs.get(credential_id)
        if blob is None:
            raise SecretNotFound(credential_id)
        return self._fernet.decrypt(blob).decode()

    def rotate(self, credential_id: str, new_plaintext: str) -> None:
        if credential_id not in self._blobs:
            raise SecretNotFound(credential_id)
        self._blobs[credential_id] = self._fernet.encrypt(new_plaintext.encode())


class VaultSecretStore:
    """HashiCorp Vault 实现(KV v2)。生产用,接口与 LocalSecretStore 一致。"""

    def __init__(self, client, mount_point: str = "secret", path_prefix: str = "yimai") -> None:
        # client: hvac.Client(已认证)。注入而非自建,便于测试与凭证管理。
        self._client = client
        self._mount = mount_point
        self._prefix = path_prefix

    def _path(self, credential_id: str) -> str:
        return f"{self._prefix}/{credential_id}"

    def put(self, name: str, plaintext: str) -> str:
        credential_id = uuid.uuid4().hex
        self._client.secrets.kv.v2.create_or_update_secret(
            path=self._path(credential_id),
            secret={"name": name, "value": plaintext},
            mount_point=self._mount,
        )
        return credential_id

    def get(self, credential_id: str) -> str:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=self._path(credential_id), mount_point=self._mount
            )
        except Exception as exc:  # hvac.exceptions.InvalidPath 等
            raise SecretNotFound(credential_id) from exc
        return resp["data"]["data"]["value"]

    def rotate(self, credential_id: str, new_plaintext: str) -> None:
        # 先确认存在,再写新版本(KV v2 自动留版本,支持双 secret 过渡)
        current = self.get(credential_id)  # 不存在则抛 SecretNotFound
        _ = current
        self._client.secrets.kv.v2.create_or_update_secret(
            path=self._path(credential_id),
            secret={"value": new_plaintext},
            mount_point=self._mount,
        )
