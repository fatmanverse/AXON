"""services 与 service_placements 的边界 schema(§14.2)。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.deployment import DeploymentStrategy
from app.models.service import ObservedStatus, ReloadMode, Runtime, ServiceEnvironment


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    env: ServiceEnvironment
    runtime: Runtime
    runtime_ref: dict[str, Any] = Field(min_length=1)
    desired_version: str | None = Field(default=None, max_length=128)
    reload_mode: ReloadMode = ReloadMode.RESTART
    health_check: dict[str, Any] | None = None


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
    env: ServiceEnvironment
    runtime: Runtime
    runtime_ref: dict[str, Any]
    desired_version: str | None = None
    reload_mode: ReloadMode
    placement_count: int = 0

    model_config = {"from_attributes": True}


class DeployRequestBody(BaseModel):
    """UI 触发部署入参(§15.2 body:{version, strategy})。env 取自服务本身,不由前端传。"""

    version: str = Field(min_length=1, max_length=128)
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = Field(default=None, max_length=64)
