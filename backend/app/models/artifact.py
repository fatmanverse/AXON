"""artifact_registries + artifacts 制品库与制品模型(构建产物的落点与寻址)。

制品库(artifact_registry)是制品的存放后端:docker registry、通用文件仓等,
凭据只存保险箱引用(不落明文,规矩同 servers.ssh_credential_id)。制品
(artifact)是一次构建的产物记录:锚定 service + git_sha + build,携带
唯一寻址(uri / digest),供部署侧凭此把制品送上目标机。

聚合关系:artifact 属于某个 registry,用真外键(聚合内组合,库删则制品记录
级联清理);对 service / build 是跨聚合软引用(字符串 id,不建外键),与全系统
service_id/build_id 的软引用规律一致。
"""

import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_cls]


def _uuid() -> str:
    return uuid.uuid4().hex


class ArtifactRegistryType(StrEnum):
    """制品库类型:docker 镜像仓 / 通用文件仓(tar 包等)。按需再扩 pypi/npm。"""

    DOCKER = "docker"
    GENERIC = "generic"


class ArtifactRegistry(Base, TimestampMixin):
    __tablename__ = "artifact_registries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    type: Mapped[ArtifactRegistryType] = mapped_column(
        Enum(ArtifactRegistryType, name="artifact_registry_type", values_callable=_enum_values),
        nullable=False,
    )
    # 库地址:docker 为 registry host(如 registry.example.com/team),generic 为基址。
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 推送/拉取凭据的保险箱引用(不落明文);公开库可空。
    credential_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 默认库:构建产物未显式指定库时的落点(至多一个为 True,由仓储层保证)。
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    # 同一库内 (name, version) 唯一:一次构建产出的镜像/包在库中有稳定坐标。
    __table_args__ = (
        UniqueConstraint(
            "registry_id", "name", "version", name="uq_artifacts_registry_name_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 真外键:制品属于某库,库删则制品记录级联清理(聚合内组合)。
    registry_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("artifact_registries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 跨聚合软引用:关联产出该制品的 service / build 与关联键 git_sha。
    service_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    build_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 制品坐标:name+version 是人类可读标识,digest 是内容寻址(sha256:...),
    # uri 是可直接拉取的完整地址(与 deployments.artifact 字符串同规格,可互填)。
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uri: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
