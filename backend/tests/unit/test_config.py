"""T0.1 验收:配置从环境变量加载(pydantic-settings)。"""

import pytest

from app.core.config import Settings


def test_settings_defaults() -> None:
    s = Settings()
    assert s.app_name
    assert s.env in {"dev", "staging", "prod"}


def test_settings_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("YIMAI_ENV", "staging")
    monkeypatch.setenv("YIMAI_LOG_JSON", "false")
    s = Settings()
    assert s.env == "staging"
    assert s.log_json is False


def _prod_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "prod",
        "database_url": "postgresql+asyncpg://prod:strong@db.internal:5432/axon",
        "redis_url": "redis://redis.internal:6379/0",
        "coordination_backend": "redis",
        "jwt_secret": "p" * 64,
        "secret_backend": "vault",
        "vault_addr": "https://vault.internal:8200",
        "vault_token": "v" * 32,
        "seed_admin_user": "ops-admin",
        "seed_admin_password": "s" * 24,
        "cors_origins": ["https://axon.example.com"],
        "webhook_secrets": {"ci-prod": "w" * 32},
    }
    values.update(overrides)
    return Settings(**values)


def test_prod_rejects_default_jwt_secret() -> None:
    settings = _prod_settings(jwt_secret="CHANGE-ME-in-prod")

    with pytest.raises(ValueError, match="jwt_secret"):
        settings.validate_for_runtime()


def test_prod_rejects_local_secret_backend_and_default_seed_password() -> None:
    settings = _prod_settings(secret_backend="local", seed_admin_password="admin")

    with pytest.raises(ValueError, match="secret_backend"):
        settings.validate_for_runtime()


def test_prod_settings_pass_validation() -> None:
    _prod_settings().validate_for_runtime()


def test_prod_requires_distributed_coordination_backend() -> None:
    settings = _prod_settings(coordination_backend="memory")

    with pytest.raises(ValueError, match="coordination_backend"):
        settings.validate_for_runtime()


def test_prod_agent_requires_mtls_material() -> None:
    settings = _prod_settings(agent_grpc_enabled=True)

    with pytest.raises(ValueError, match="agent_grpc_tls_enabled"):
        settings.validate_for_runtime()


def test_prod_agent_requires_all_mtls_files() -> None:
    settings = _prod_settings(
        agent_grpc_enabled=True,
        agent_grpc_tls_enabled=True,
        agent_grpc_server_cert_file="/run/secrets/agent-server.crt",
    )

    with pytest.raises(ValueError, match="agent_grpc_server_key_file"):
        settings.validate_for_runtime()


def test_prod_agent_accepts_complete_mtls_config() -> None:
    settings = _prod_settings(
        agent_grpc_enabled=True,
        agent_grpc_tls_enabled=True,
        agent_grpc_server_cert_file="/run/secrets/agent-server.crt",
        agent_grpc_server_key_file="/run/secrets/agent-server.key",
        agent_grpc_client_ca_file="/run/secrets/agent-client-ca.crt",
        agent_grpc_server_address="agent-grpc.internal:50051",
    )

    settings.validate_for_runtime()
