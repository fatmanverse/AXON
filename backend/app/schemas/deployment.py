"""deployments 的输出 schema 与 webhook 入参(§14.3 / §15.4 / §8.2)。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.deployment import DeploymentSource, DeploymentStatus, DeploymentStrategy


class DeploymentOut(BaseModel):
    """部署记录视图(供部署历史 / 主页 feed)。"""

    id: str
    service_id: str
    env: str
    git_sha: str | None = None
    version: str | None = None
    artifact: str | None = None
    strategy: DeploymentStrategy
    source: DeploymentSource
    pipeline_id: str | None = None
    pipeline_url: str | None = None
    operator: str | None = None
    status: DeploymentStatus
    previous_deployment_id: str | None = None
    scan_result_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class DeploymentWebhookPayload(BaseModel):
    """流水线上报的部署事件(§8.2)。service 是服务标识,pipeline_id 与 service+env
    构成幂等键(§8.3 ②)。status 限 running/success/failed(rolled_back 由控制面
    自身回滚闭环产生,不接受外部上报)。"""

    service: str = Field(min_length=1, max_length=128)
    env: str = Field(min_length=1, max_length=16)
    pipeline_id: str = Field(min_length=1, max_length=128)
    status: DeploymentStatus
    git_sha: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=128)
    artifact: str | None = Field(default=None, max_length=512)
    pipeline_url: str | None = Field(default=None, max_length=512)
    operator: str | None = Field(default=None, max_length=128)
    finished_at: datetime | None = None
