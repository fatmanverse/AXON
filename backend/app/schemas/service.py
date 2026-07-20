"""services 与 service_placements 的边界 schema(§14.2)。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.deployment import DeploymentStrategy
from app.models.service import ObservedStatus, ReloadMode, Runtime


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # env 为环境 name 字符串(引用 environments 表);由 API 层软校验该环境存在。
    # 不再受 dev/staging/prod 枚举约束,可为任意已创建的自定义环境名。
    env: str = Field(min_length=1, max_length=64)
    runtime: Runtime
    runtime_ref: dict[str, Any] = Field(min_length=1)
    desired_version: str | None = Field(default=None, max_length=128)
    reload_mode: ReloadMode = ReloadMode.RESTART
    health_check: dict[str, Any] | None = None
    # 构建默认配置(照 health_check 先例的可空 JSON):承载 repo_url / git_ref /
    # test_command / build_command / artifact_type(generic|docker)及形态专属字段
    # (generic 的 output_path、docker 的 image_name/dockerfile/registry_id)。
    # 触发构建时以此为默认,可被 BuildRequestBody 覆写。
    build_config: dict[str, Any] | None = None


class PlacementCreate(BaseModel):
    service_id: str = Field(min_length=32, max_length=32)
    server_id: str | None = Field(default=None, min_length=32, max_length=32)
    observed_version: str | None = Field(default=None, max_length=128)
    observed_status: ObservedStatus = ObservedStatus.UNKNOWN
    last_seen_at: datetime | None = None


class ServiceOut(BaseModel):
    """服务列表/详情视图(§15.4)。placement_count 让列表页无需再拉放置即可展示规模。"""

    id: str
    name: str
    env: str
    runtime: Runtime
    runtime_ref: dict[str, Any]
    desired_version: str | None = None
    reload_mode: ReloadMode
    placement_count: int = 0

    model_config = {"from_attributes": True}


class DeployRequestBody(BaseModel):
    """UI 触发部署入参。version 走 CI,artifact_id 走制品直发；env 取自服务本身。"""

    version: str | None = Field(default=None, max_length=128)
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = Field(default=None, max_length=64)
    artifact_id: str | None = Field(default=None, min_length=32, max_length=32)

    @model_validator(mode="after")
    def require_version_or_artifact(self) -> "DeployRequestBody":
        if self.artifact_id is None and not self.version:
            raise ValueError("CI 部署需 version")
        return self


class PromoteRequestBody(BaseModel):
    """环境晋升入参(§10.3):把 source_service_id(如 staging)最近一次成功部署的
    制品晋升到当前(目标)服务。source 与目标须同名不同 env(如 billing staging→prod)。"""

    source_service_id: str = Field(min_length=1, max_length=32)
