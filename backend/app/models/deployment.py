"""deployments 部署记录模型与状态机(§14.3 「焊点」)。

deployment 是把「提交→扫描→部署→监控」串起来的关联键载体:一条记录锚定
service + git_sha + env,并携带 previous_deployment_id(支持历史版本回滚)与
scan_result_id(交付态关联,Epic 3 回填)。

状态机规则:running → success / failed / rolled_back。三者均为终态,**只前进
不回退**——一次部署的结局定了就不再变;回滚是"重部署上一版"生成的**新记录**,
原记录落 rolled_back 闭环(§11.2),而非把旧记录改回 running。
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


class DeploymentStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"

    def is_terminal(self) -> bool:
        return self in _TERMINAL


class DeploymentStrategy(StrEnum):
    ROLLING = "rolling"
    CANARY = "canary"
    BLUE_GREEN = "blue-green"
    RECREATE = "recreate"


class DeploymentSource(StrEnum):
    UI_TRIGGERED = "ui-triggered"
    PIPELINE_WEBHOOK = "pipeline-webhook"
    MANUAL = "manual"


# 终态:成功/失败/被回滚。都不可再转出(只前进不回退)。
_TERMINAL: frozenset[DeploymentStatus] = frozenset(
    {DeploymentStatus.SUCCESS, DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK}
)

# success 可再转 rolled_back:回滚是 success 之后的正当闭环(§11.2),不算"回退"。
# failed / rolled_back 为完全终态。is_terminal 仍含 success(用于盖 finished_at)。
_ALLOWED: dict[DeploymentStatus, frozenset[DeploymentStatus]] = {
    DeploymentStatus.RUNNING: _TERMINAL,
    DeploymentStatus.SUCCESS: frozenset({DeploymentStatus.ROLLED_BACK}),
    DeploymentStatus.FAILED: frozenset(),
    DeploymentStatus.ROLLED_BACK: frozenset(),
}


def can_transition(src: DeploymentStatus, dst: DeploymentStatus) -> bool:
    return dst in _ALLOWED[src]


def ensure_transition(src: DeploymentStatus, dst: DeploymentStatus) -> None:
    if not can_transition(src, dst):
        raise ValueError(f"非法状态流转: {src.value} → {dst.value}")


def _uuid() -> str:
    return uuid.uuid4().hex


class Deployment(Base, TimestampMixin):
    __tablename__ = "deployments"
    # webhook 幂等键(§8.3 ②):同一 (pipeline_id, service, env) 只留一条,重复上报
    # 收敛为幂等更新。pipeline_id 可空,SQL 中 NULL 不参与唯一判断,故 UI 触发尚未
    # 回填 pipeline_id 的 running 记录不受约束,只有带 pipeline_id 的上报走去重。
    __table_args__ = (
        UniqueConstraint("pipeline_id", "service_id", "env", name="uq_deployments_idempotency"),
    )

    # id 即 deployment_id 关联键(§14.3)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 长度与 environments.name / services.env 对齐(64),避免自定义长环境名截断。
    env: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # git_sha 是贯穿扫描/部署的关联键;version 是人类可读的 tag。
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact: Mapped[str | None] = mapped_column(String(512), nullable=True)
    strategy: Mapped[DeploymentStrategy] = mapped_column(
        Enum(DeploymentStrategy, name="deployment_strategy", values_callable=_enum_values),
        nullable=False,
        default=DeploymentStrategy.ROLLING,
    )
    source: Mapped[DeploymentSource] = mapped_column(
        Enum(DeploymentSource, name="deployment_source", values_callable=_enum_values),
        nullable=False,
    )
    pipeline_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus, name="deployment_status", values_callable=_enum_values),
        nullable=False,
        default=DeploymentStatus.RUNNING,
        index=True,
    )
    # 上一次成功部署(支持兼容 previous 回滚);scan_result_id 由 Epic 3 按 git_sha 回填。
    previous_deployment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scan_result_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 新链路:控制面自建构建产出的制品(软引用 artifacts.id)。旧链路继续填 artifact
    # 字符串地址,新链路填 artifact_id,两者并存、零破坏(见迁移说明)。
    artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
