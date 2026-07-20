"""artifact_registries + artifacts 数据访问层(构建能力一期)。

制品库(registry):ensure_default_registry 幂等取/建唯一 is_default 库(generic
形态用,构建产物未显式指定库时的落点);docker 库需用户经 API 显式配(带 url +
凭据引用)。create_registry/list/delete 支撑管理。
制品(artifact):create_artifact 落一次构建产物;list_for_service 供构建产物视图。

凭据一律只存保险箱引用(credential_id),不落明文(§13,规矩同 servers)。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.artifact import Artifact, ArtifactRegistry, ArtifactRegistryType

# 默认 generic 库的固定名字:未显式指定库时构建产物的落点。
_DEFAULT_GENERIC_NAME = "default-generic"


class ArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── registry ────────────────────────────────────────────────────

    async def ensure_default_registry(
        self, type_: ArtifactRegistryType = ArtifactRegistryType.GENERIC
    ) -> ArtifactRegistry:
        """幂等取/建唯一默认库。已有默认库直接返回,否则建一个 is_default 的库。"""
        existing = await self._find_default()
        if existing is not None:
            return existing
        registry = ArtifactRegistry(
            name=_DEFAULT_GENERIC_NAME,
            type=type_,
            url="",
            is_default=True,
            description="默认制品库(generic 形态构建产物落点)",
        )
        self._session.add(registry)
        await self._session.flush()
        return registry

    async def get_registry(self, registry_id: str) -> ArtifactRegistry:
        registry = await self._session.get(ArtifactRegistry, registry_id)
        if registry is None:
            raise AppError("artifact_registry_not_found", "制品库不存在", status_code=404)
        return registry

    async def create_registry(
        self,
        *,
        name: str,
        type_: ArtifactRegistryType,
        url: str = "",
        credential_id: str | None = None,
        description: str = "",
    ) -> ArtifactRegistry:
        registry = ArtifactRegistry(
            name=name,
            type=type_,
            url=url,
            credential_id=credential_id,
            is_default=False,
            description=description,
        )
        self._session.add(registry)
        await self._session.flush()
        return registry

    async def list_registries(self) -> Sequence[ArtifactRegistry]:
        result = await self._session.execute(
            select(ArtifactRegistry).order_by(ArtifactRegistry.name)
        )
        return result.scalars().all()

    async def delete_registry(self, registry_id: str) -> None:
        registry = await self.get_registry(registry_id)
        await self._session.delete(registry)
        await self._session.flush()

    # ── artifact ──────────────────────────────────────────────────────

    async def get_artifact(self, artifact_id: str) -> Artifact:
        artifact = await self._session.get(Artifact, artifact_id)
        if artifact is None:
            raise AppError("artifact_not_found", "制品不存在", status_code=404)
        return artifact

    async def create_artifact(
        self,
        *,
        registry_id: str,
        service_id: str,
        name: str,
        uri: str,
        version: str | None = None,
        digest: str | None = None,
        build_id: str | None = None,
        git_sha: str | None = None,
        size_bytes: int | None = None,
        meta: dict | None = None,
    ) -> Artifact:
        artifact = Artifact(
            registry_id=registry_id,
            service_id=service_id,
            build_id=build_id,
            git_sha=git_sha,
            name=name,
            version=version,
            digest=digest,
            uri=uri,
            size_bytes=size_bytes,
            meta=meta,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def list_for_service(self, service_id: str, *, limit: int = 50) -> Sequence[Artifact]:
        stmt = (
            select(Artifact)
            .where(Artifact.service_id == service_id)
            .order_by(Artifact.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def _find_default(self) -> ArtifactRegistry | None:
        stmt = select(ArtifactRegistry).where(ArtifactRegistry.is_default.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().first()
