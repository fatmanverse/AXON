"""scan_results 代码扫描结果模型(§14.4)。

扫描工具(SonarQube/Semgrep/Trivy)扫完经 webhook 上报,按 git_sha 挂到链路上,
供部署前质量门禁查询(§7.2)。幂等键 (git_sha, scanner):同一提交同一扫描器
重复上报收敛为幂等更新(仓储层保证)。敏感信息不落表。
"""

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


class Scanner(StrEnum):
    SONARQUBE = "sonarqube"
    SEMGREP = "semgrep"
    TRIVY = "trivy"


def _uuid() -> str:
    return uuid.uuid4().hex


class ScanResult(Base, TimestampMixin):
    __tablename__ = "scan_results"
    # 幂等键(§8.3):同一 (git_sha, scanner) 只留一条,重复上报幂等更新。
    __table_args__ = (
        UniqueConstraint("git_sha", "scanner", name="uq_scan_results_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # git_sha 是贯穿扫描/部署的关联键(§14.8)
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scanner: Mapped[Scanner] = mapped_column(
        Enum(Scanner, name="scan_scanner", values_callable=_enum_values),
        nullable=False,
    )
    critical: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # passed 是扫描器自身给的门禁结论;控制面的部署卡点策略另算(§7.2)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    report_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
