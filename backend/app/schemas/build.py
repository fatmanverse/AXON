"""构建能力边界 schema(build / build_node / artifact / registry)。

命名同全系约定:XxxOut 输出视图(model_config from_attributes),XxxCreate 输入,
XxxRequestBody 动作端点体。凭据一律只收明文换 vault id,Out 绝不含密钥(§13,
规矩同 servers.ServerOut)。字段宽度对齐模型列宽。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.artifact import ArtifactRegistryType
from app.models.build import BuildSource, BuildStatus
from app.models.build_node import BuildNodeStatus


class BuildOut(BaseModel):
    """构建记录视图(供构建历史 / 详情)。"""

    id: str
    service_id: str
    repo_url: str | None = None
    git_ref: str | None = None
    git_sha: str | None = None
    version: str | None = None
    build_node_id: str | None = None
    artifact_id: str | None = None
    source: BuildSource
    pipeline_id: str | None = None
    pipeline_url: str | None = None
    operator: str | None = None
    status: BuildStatus
    log_url: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class BuildRequestBody(BaseModel):
    """UI 触发构建入参:可覆盖服务 build_config 的默认 ref/version;缺省则用配置默认。"""

    git_ref: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=128)


class BuildNodeOut(BaseModel):
    """构建节点视图。ssh_credential_id 仅作引用暴露,绝不含凭据明文。"""

    id: str
    name: str
    server_id: str | None = None
    host: str | None = None
    ssh_credential_id: str | None = None
    status: BuildNodeStatus
    labels: dict = Field(default_factory=dict)
    max_concurrent: int
    last_heartbeat_at: datetime | None = None

    model_config = {"from_attributes": True}


class BuildNodeCreate(BaseModel):
    """注册本地或 SSH 构建节点；外部节点需提供 host 与凭证引用。"""

    name: str = Field(min_length=1, max_length=128)
    server_id: str | None = Field(default=None, max_length=32)
    host: str | None = Field(default=None, max_length=255)
    ssh_credential_id: str | None = Field(default=None, max_length=128)
    labels: dict = Field(default_factory=dict)
    max_concurrent: int = Field(default=1, ge=1, le=64)


class ArtifactOut(BaseModel):
    """制品视图(构建产物的坐标与寻址)。"""

    id: str
    registry_id: str
    service_id: str
    build_id: str | None = None
    git_sha: str | None = None
    name: str
    version: str | None = None
    digest: str | None = None
    uri: str
    size_bytes: int | None = None

    model_config = {"from_attributes": True}


class ArtifactRegistryOut(BaseModel):
    """制品库视图。credential_id 仅作引用暴露,绝不含凭据明文。"""

    id: str
    name: str
    type: ArtifactRegistryType
    url: str
    credential_id: str | None = None
    is_default: bool
    description: str

    model_config = {"from_attributes": True}


class ArtifactRegistryCreate(BaseModel):
    """建制品库入参。docker 库配 url;credential 收明文由 API 换 vault id(不落明文)。"""

    name: str = Field(min_length=1, max_length=128)
    type: ArtifactRegistryType
    url: str = Field(default="", max_length=512)
    credential: str | None = Field(default=None, max_length=4096)
    description: str = Field(default="", max_length=512)
