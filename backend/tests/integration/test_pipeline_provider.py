"""pipeline provider 生产工厂验收(T2.7,设计 §8.1)。

生产环境部署要触发真实 CI,但 get_pipeline_adapter_provider 之前只读
app.state,生产从未设置 → 永远 None → 部署 501。本工厂按 service 从配置
(settings.pipeline_config)构造对应 Jenkins/GitLab adapter,平台 token 走保险箱。

覆盖:
- 按 service.name 命中专属配置 → 构造对应 provider(Jenkins/GitLab)。
- 无专属配置但有 `*` 默认项 → 用默认。
- 既无专属也无默认 → 返回 None(上层据此报"未配置 CI",不 500)。
- 未知 provider 类型 → 抛配置错误(启动即暴露,不拖到运行期)。
"""

from __future__ import annotations

from app.adapters.pipeline import GitLabPipelineAdapter, JenkinsPipelineAdapter
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.service import Runtime, Service, ServiceEnvironment
from app.services.pipeline_provider import build_pipeline_provider


def _service(name: str) -> Service:
    return Service(
        name=name,
        env=ServiceEnvironment.PROD,
        runtime=Runtime.SYSTEMD,
        runtime_ref={"unit_name": f"{name}.service"},
    )


def _secrets() -> LocalSecretStore:
    return LocalSecretStore(master_key=generate_master_key())


def test_builds_jenkins_for_matched_service():
    secrets = _secrets()
    cfg = {
        "billing": {
            "provider": "jenkins",
            "base_url": "https://ci.example.com",
            "username": "svc",
            "token_credential_id": "cred-1",
        }
    }
    provider = build_pipeline_provider(cfg, secrets)
    adapter = provider(_service("billing"))
    assert isinstance(adapter, JenkinsPipelineAdapter)


def test_builds_gitlab_for_matched_service():
    secrets = _secrets()
    cfg = {
        "web": {
            "provider": "gitlab",
            "base_url": "https://gitlab.example.com",
            "project_id": "42",
            "trigger_token_credential_id": "cred-2",
        }
    }
    provider = build_pipeline_provider(cfg, secrets)
    adapter = provider(_service("web"))
    assert isinstance(adapter, GitLabPipelineAdapter)


def test_falls_back_to_wildcard_default():
    secrets = _secrets()
    cfg = {
        "*": {
            "provider": "gitlab",
            "base_url": "https://gitlab.example.com",
            "project_id": "1",
            "trigger_token_credential_id": "cred-x",
        }
    }
    provider = build_pipeline_provider(cfg, secrets)
    adapter = provider(_service("anything"))
    assert isinstance(adapter, GitLabPipelineAdapter)


def test_returns_none_when_no_match_and_no_default():
    secrets = _secrets()
    provider = build_pipeline_provider({}, secrets)
    assert provider(_service("billing")) is None


def test_specific_service_overrides_wildcard():
    secrets = _secrets()
    cfg = {
        "*": {
            "provider": "gitlab",
            "base_url": "https://gl",
            "project_id": "1",
            "trigger_token_credential_id": "c",
        },
        "billing": {
            "provider": "jenkins",
            "base_url": "https://jk",
            "username": "u",
            "token_credential_id": "c2",
        },
    }
    provider = build_pipeline_provider(cfg, secrets)
    assert isinstance(provider(_service("billing")), JenkinsPipelineAdapter)
    assert isinstance(provider(_service("other")), GitLabPipelineAdapter)


def test_unknown_provider_raises_at_build_time():
    secrets = _secrets()
    cfg = {"billing": {"provider": "bamboo", "base_url": "x"}}
    provider = build_pipeline_provider(cfg, secrets)
    import pytest

    from app.core.errors import AppError

    with pytest.raises(AppError):
        provider(_service("billing"))
