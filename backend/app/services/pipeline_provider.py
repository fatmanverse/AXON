"""pipeline provider 生产工厂(T2.7,设计 §8.1)。

按 service 解析出该用哪个 PipelineAdapter,是 deps.get_pipeline_adapter_provider
的生产实现。之前该 provider 只读 app.state,生产从未设置 → 永远 None → 部署直接
501。本工厂从 settings.pipeline_config(JSON)按 service.name 构造对应 adapter,
平台 token 走凭证保险箱(§13,不硬编码)。

配置形态(settings.pipeline_config):
    {
      "*": {...},                    # 可选默认项,未命中专属配置时兜底
      "billing": {                   # 按 service.name 精确匹配,优先于 "*"
        "provider": "jenkins",       # jenkins | gitlab
        "base_url": "https://ci...",
        "username": "svc",           # jenkins 需要
        "token_credential_id": "..." # jenkins:API token 的保险箱 id
      },
      "web": {
        "provider": "gitlab",
        "base_url": "https://gitlab...",
        "project_id": "42",
        "trigger_token_credential_id": "..."  # gitlab:trigger token 的保险箱 id
      }
    }

设计取舍:
- 精确 service 名优先于 "*" 默认(specific overrides general)。
- 无匹配且无默认 → provider 返回 None,上层(deploy 端点)据此报"未配置 CI"而非
  500,保持 MVP 未配 CI 时的既有语义。
- 未知 provider 类型在**构造该 adapter 时**抛 AppError:配置错误尽早暴露,而非
  拖到部署运行期才炸。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.pipeline import (
    GitLabPipelineAdapter,
    JenkinsPipelineAdapter,
    PipelineAdapter,
)
from app.core.errors import AppError
from app.core.secrets import SecretStore
from app.models.service import Service

# 与 DeploymentService.AdapterProvider 一致:按 service 返回 adapter,或 None(未配)。
Provider = Callable[[Service], PipelineAdapter | None]


def _build_adapter(cfg: dict[str, Any], secrets: SecretStore) -> PipelineAdapter:
    """按单条配置构造一个 adapter。未知 provider 抛 AppError(配置错误尽早暴露)。"""
    provider_type = cfg.get("provider")
    if provider_type == "jenkins":
        return JenkinsPipelineAdapter(
            base_url=cfg["base_url"],
            username=cfg.get("username", ""),
            token_credential_id=cfg["token_credential_id"],
            secret_store=secrets,
        )
    if provider_type == "gitlab":
        return GitLabPipelineAdapter(
            base_url=cfg["base_url"],
            project_id=str(cfg["project_id"]),
            trigger_token_credential_id=cfg["trigger_token_credential_id"],
            secret_store=secrets,
        )
    raise AppError(
        "pipeline_config_invalid",
        f"未知的 CI provider 类型: {provider_type!r}",
        status_code=500,
    )


def build_pipeline_provider(
    pipeline_config: dict[str, dict[str, Any]],
    secrets: SecretStore,
) -> Provider:
    """构造生产 pipeline provider。按 service.name 命中配置(精确优先于 `*` 默认)。

    返回的 provider 闭包在每次调用时按 service 现构造 adapter——adapter 无状态
    (token 每次从保险箱现取),不缓存,避免 token 轮转后用到旧值。
    """

    def _provider(service: Service) -> PipelineAdapter | None:
        cfg = pipeline_config.get(service.name) or pipeline_config.get("*")
        if cfg is None:
            return None
        return _build_adapter(cfg, secrets)

    return _provider
