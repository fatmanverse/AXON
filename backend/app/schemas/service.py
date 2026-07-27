"""services 与 service_placements 的边界 schema(§14.2)。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.deployment import DeploymentStrategy
from app.models.service import ObservedStatus, ReloadMode, Runtime
from app.schemas.build_config import BuildConfigModel


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
    # 构建默认配置:结构化校验(编写即校验),承载 repo_url / build_command /
    # artifact_type(generic|docker)及形态专属字段(generic 的 output_path、
    # docker 的 image_ref/dockerfile)。触发构建时 git_ref/version 可被覆写。
    # 提供即按形态校验必填项;不配则该服务不支持本地构建。
    build_config: BuildConfigModel | None = None


class ServiceUpdate(BaseModel):
    """服务的部分更新入参(PATCH)。仅提供的字段被更新,未提供的保持原值。

    build_config 用结构化模型:一旦提供就整体替换并按形态校验必填项——避免
    "只改一半"留下残缺配置。传显式 null 语义由 API 层区分(未提供 vs 置空),
    这里所有字段默认 None 表示"未提供"。
    """

    desired_version: str | None = Field(default=None, max_length=128)
    reload_mode: ReloadMode | None = None
    health_check: dict[str, Any] | None = None
    build_config: BuildConfigModel | None = None


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
    # 构建配置原样透传(dict 而非 BuildConfigModel):历史服务可能存有残缺配置,
    # 输出侧不做形态校验,以免读取既有数据时报错——校验只在写入(Create/Update)时做。
    build_config: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class DeployRequestBody(BaseModel):
    """UI 触发部署入参。version 与 artifact_id 必须且只能提供一个：
    - CI 模式：传 version（原有路径，触发外部 CI 流水线）。
    - artifact 直发模式：传 artifact_id（直接把已登记制品送上 runtime）。
    env 取自服务本身，不由前端传。
    """

    version: str | None = Field(default=None, min_length=1, max_length=128)
    artifact_id: str | None = Field(default=None, min_length=32, max_length=32)
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_version_or_artifact(self) -> "DeployRequestBody":
        """version 与 artifact_id 必须且只能提供一个；两者缺失或同时存在均报错。"""
        has_version = bool(self.version)
        has_artifact = self.artifact_id is not None
        if has_version == has_artifact:
            raise ValueError("version 与 artifact_id 必须且只能提供一个")
        return self


class RollbackRequestBody(BaseModel):
    """可选指定历史 deployment；省略时沿当前记录的 previous 链。"""

    target_deployment_id: str | None = Field(default=None, min_length=32, max_length=32)


class PromoteRequestBody(BaseModel):
    """环境晋升入参(§10.3):把 source_service_id(如 staging)最近一次成功部署的
    制品晋升到当前(目标)服务。source 与目标须同名不同 env(如 billing staging→prod)。"""

    source_service_id: str = Field(min_length=1, max_length=32)
