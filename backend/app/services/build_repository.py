"""builds 数据访问层(构建能力一期,§方案 A)。

镜像 DeploymentRepository 的状态机仓储范式:create 落 pending+started_at、get
不存在抛 404、mark_status 经 Build 模型自带的 ensure_transition 守卫(非法流转
抛 ValueError,落终态盖 finished_at)、set_artifact 成功后回填产出制品、
list_for_service 倒序限量。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.build import Build, BuildSource, BuildStatus, ensure_transition


class BuildRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        service_id: str,
        source: BuildSource,
        repo_url: str | None = None,
        git_ref: str | None = None,
        git_sha: str | None = None,
        version: str | None = None,
        build_node_id: str | None = None,
        pipeline_id: str | None = None,
        pipeline_url: str | None = None,
        operator: str | None = None,
    ) -> Build:
        build = Build(
            service_id=service_id,
            source=source,
            repo_url=repo_url,
            git_ref=git_ref,
            git_sha=git_sha,
            version=version,
            build_node_id=build_node_id,
            pipeline_id=pipeline_id,
            pipeline_url=pipeline_url,
            operator=operator,
            status=BuildStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        self._session.add(build)
        await self._session.flush()
        return build

    async def get(self, build_id: str) -> Build:
        build = await self._session.get(Build, build_id)
        if build is None:
            raise AppError("build_not_found", "构建记录不存在", status_code=404)
        return build

    async def list_for_service(self, service_id: str, *, limit: int = 50) -> Sequence[Build]:
        stmt = (
            select(Build)
            .where(Build.service_id == service_id)
            .order_by(Build.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def mark_status(
        self, build_id: str, status: BuildStatus, *, error: str | None = None
    ) -> Build:
        """流转构建状态。经状态机守卫:非法流转抛 ValueError;落终态盖 finished_at。"""
        build = await self.get(build_id)
        ensure_transition(build.status, status)
        build.status = status
        if error is not None:
            build.error = error
        if status.is_terminal():
            build.finished_at = datetime.now(UTC)
        await self._session.flush()
        return build

    async def set_artifact(self, build_id: str, artifact_id: str) -> Build:
        """构建成功后回填产出制品 id(先例:deployments.scan_result_id 回填模式)。"""
        build = await self.get(build_id)
        build.artifact_id = artifact_id
        await self._session.flush()
        return build

    async def set_git_sha(self, build_id: str, git_sha: str) -> Build:
        """clone 后回填解析出的具体 git_sha(启动时可能是分支名)。"""
        build = await self.get(build_id)
        build.git_sha = git_sha
        await self._session.flush()
        return build
