"""builds 构建记录模型与状态机(构建→部署链路的前半段「焊点」)。

一条 build 锚定 service + git_sha,记录一次「clone → 测试 → 构建 → 产出制品」的
全过程与产物。成功后回填 artifact_id 指向产出制品(artifacts 表),部署侧凭
制品完成真实下发——与 deployments 的 previous_deployment_id 回滚链路衔接,
构成「代码 → 制品 → 上线」的完整可回溯链条。

构建有两个来源:控制面自建(本地构建节点执行,source=ui-triggered)与外部 CI
上报(source=pipeline-webhook)。幂等键与 deployments 同款语义:带 pipeline_id
的上报按 (pipeline_id, service_id, git_sha) 去重,NULL 不参与唯一判定。

状态机:pending → running → success / failed / canceled,三者均终态,只前进
不回退。canceled 支持用户主动取消排队/进行中的构建。
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class BuildStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"

    def is_terminal(self) -> bool:
        return self in _TERMINAL


class BuildSource(StrEnum):
    """构建来源:UI 触发的本地构建 / 外部 CI webhook 上报 / 手工补录。"""

    UI_TRIGGERED = "ui-triggered"
    PIPELINE_WEBHOOK = "pipeline-webhook"
    MANUAL = "manual"


_TERMINAL: frozenset[BuildStatus] = frozenset(
    {BuildStatus.SUCCESS, BuildStatus.FAILED, BuildStatus.CANCELED}
)

# pending 可直接 canceled(排队中取消);running 只能走向终态;终态不可再转。
_ALLOWED: dict[BuildStatus, frozenset[BuildStatus]] = {
    BuildStatus.PENDING: frozenset({BuildStatus.RUNNING, BuildStatus.CANCELED}),
    BuildStatus.RUNNING: _TERMINAL,
    BuildStatus.SUCCESS: frozenset(),
    BuildStatus.FAILED: frozenset(),
    BuildStatus.CANCELED: frozenset(),
}


def can_transition(src: BuildStatus, dst: BuildStatus) -> bool:
    return dst in _ALLOWED[src]


def ensure_transition(src: BuildStatus, dst: BuildStatus) -> None:
    if not can_transition(src, dst):
        raise ValueError(f"非法状态流转: {src.value} → {dst.value}")


class Build(Base, TimestampMixin):
    __tablename__ = "builds"
    # 外部 CI 上报幂等键(语义同 uq_deployments_idempotency):同一 (pipeline_id,
    # service_id, git_sha) 只留一条;pipeline_id 为 NULL(本地构建)不参与判定。
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id", "service_id", "git_sha", name="uq_builds_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # git 仓库与代码基点:repo_url 支撑「填仓库地址即构建」;git_sha 是贯穿
    # 扫描/构建/部署的关联键,构建启动时可为分支名,clone 后回填为具体 sha。
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 本地构建时指向执行节点;外部 CI 上报为空。
    build_node_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 成功后回填产出制品(先例:deployments.scan_result_id 的回填模式)。
    artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[BuildSource] = mapped_column(
        Enum(BuildSource, name="build_source", values_callable=_enum_values),
        nullable=False,
    )
    pipeline_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[BuildStatus] = mapped_column(
        Enum(BuildStatus, name="build_status", values_callable=_enum_values),
        nullable=False,
        default=BuildStatus.PENDING,
        index=True,
    )
    # 构建全量日志的存放位置(远端日志文件路径或下载 URL),错误摘要另存 error。
    log_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
