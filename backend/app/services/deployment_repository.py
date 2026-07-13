"""deployments 数据访问层(T2.1,§14.3)。

创建(默认 running,盖 started_at)、按 id 取、按 service+env 倒序列表、受状态机
守卫的流转、查最近一次成功部署(回滚取上一版制品用)。状态机只前进不回退,
mark_status 经 ensure_transition 拒绝非法流转。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import realtime
from app.core.errors import AppError
from app.models.deployment import (
    Deployment,
    DeploymentSource,
    DeploymentStatus,
    DeploymentStrategy,
    ensure_transition,
)


def _as_utc(dt: datetime) -> datetime:
    """把 datetime 归一到 aware(UTC)。naive(如 sqlite 取回的)视为 UTC。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class DeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        service_id: str,
        env: str,
        source: DeploymentSource,
        strategy: DeploymentStrategy = DeploymentStrategy.ROLLING,
        git_sha: str | None = None,
        version: str | None = None,
        artifact: str | None = None,
        pipeline_id: str | None = None,
        pipeline_url: str | None = None,
        operator: str | None = None,
        previous_deployment_id: str | None = None,
        scan_result_id: str | None = None,
    ) -> Deployment:
        deployment = Deployment(
            service_id=service_id,
            env=env,
            source=source,
            strategy=strategy,
            git_sha=git_sha,
            version=version,
            artifact=artifact,
            pipeline_id=pipeline_id,
            pipeline_url=pipeline_url,
            operator=operator,
            previous_deployment_id=previous_deployment_id,
            scan_result_id=scan_result_id,
            status=DeploymentStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._session.add(deployment)
        await self._session.flush()
        realtime.enqueue_deployment(deployment)
        return deployment

    async def get(self, deployment_id: str) -> Deployment:
        deployment = await self._session.get(Deployment, deployment_id)
        if deployment is None:
            raise AppError("deployment_not_found", "部署记录不存在", status_code=404)
        return deployment

    async def list_for_service(
        self, service_id: str, *, env: str | None = None, limit: int = 50
    ) -> Sequence[Deployment]:
        """按 service(可选 env)列出部署,最新在前(供主页 feed 与部署历史)。"""
        stmt = select(Deployment).where(Deployment.service_id == service_id)
        if env is not None:
            stmt = stmt.where(Deployment.env == env)
        stmt = stmt.order_by(Deployment.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_recent(self, *, env: str | None = None, limit: int = 20) -> Sequence[Deployment]:
        """跨 service 列出最近部署,最新在前(供主页 Dashboard 的部署 feed,§9.2)。"""
        stmt = select(Deployment)
        if env is not None:
            stmt = stmt.where(Deployment.env == env)
        stmt = stmt.order_by(Deployment.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def mark_status(self, deployment_id: str, status: DeploymentStatus) -> Deployment:
        """流转部署状态。经状态机守卫:非法流转抛 ValueError;落终态盖 finished_at。"""
        deployment = await self.get(deployment_id)
        ensure_transition(deployment.status, status)
        deployment.status = status
        if status.is_terminal():
            deployment.finished_at = datetime.now(UTC)
        await self._session.flush()
        realtime.enqueue_deployment(deployment)
        return deployment

    async def list_running(self, *, limit: int = 200) -> Sequence[Deployment]:
        """列出所有仍处 running 的部署(跨 service),供轮询兜底补齐终态(§8.2)。

        最旧在前:优先补偿卡得最久的记录。上限防一次拉取过多,补偿是周期任务,
        未覆盖的下一轮继续。
        """
        stmt = (
            select(Deployment)
            .where(Deployment.status == DeploymentStatus.RUNNING)
            .order_by(Deployment.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def latest_successful(self, service_id: str, *, env: str) -> Deployment | None:
        """查该 service+env 最近一次成功部署(回滚取其 artifact 作上一版)。"""
        stmt = (
            select(Deployment)
            .where(
                Deployment.service_id == service_id,
                Deployment.env == env,
                Deployment.status == DeploymentStatus.SUCCESS,
            )
            .order_by(Deployment.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_idempotency(
        self, *, pipeline_id: str, service_id: str, env: str
    ) -> Deployment | None:
        """按 webhook 幂等键 (pipeline_id, service, env) 查已存在记录(§8.3 ②)。"""
        stmt = select(Deployment).where(
            Deployment.pipeline_id == pipeline_id,
            Deployment.service_id == service_id,
            Deployment.env == env,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_from_webhook(
        self,
        *,
        service_id: str,
        env: str,
        pipeline_id: str,
        status: DeploymentStatus,
        git_sha: str | None = None,
        version: str | None = None,
        artifact: str | None = None,
        pipeline_url: str | None = None,
        operator: str | None = None,
        finished_at: datetime | None = None,
    ) -> Deployment:
        """幂等落一条 webhook 上报的部署记录(§8.3 ②③)。

        首次上报 INSERT;同幂等键重复上报 UPDATE 同一条。乱序保护:带 finished_at
        的旧事件(finished_at 早于已存记录)直接丢弃,不覆盖较新状态——避免重试导致
        running 晚于 success 到达时把状态改回去。webhook 上报的是已知结局,故 source
        标记为 pipeline-webhook,状态直接落传入值(不经 running 中转)。

        并发安全(§8.3 ②):等价于 INSERT ... ON CONFLICT DO UPDATE,但用可移植的
        savepoint(begin_nested)包裹 INSERT——撞唯一约束(两个首发上报并发到达)时
        回退为 find+update,而非抛错。不写死方言特定的 ON CONFLICT 语法,守住"换库
        零改代码"目标;savepoint 对 asyncpg 必需——否则 IntegrityError 会污染整个
        事务(current transaction is aborted),后续写全失败。
        """
        # 快路径:已存在则直接更新(重复上报是常态,免去无谓的 INSERT 尝试)。
        existing = await self.find_by_idempotency(
            pipeline_id=pipeline_id, service_id=service_id, env=env
        )
        if existing is not None:
            return await self._apply_webhook_update(
                existing,
                status=status,
                git_sha=git_sha,
                version=version,
                artifact=artifact,
                pipeline_url=pipeline_url,
                operator=operator,
                finished_at=finished_at,
            )

        # 首发上报:在 savepoint 内尝试 INSERT。并发下只有一个成功,另一个撞唯一约束
        # 回退到 update——达成 ON CONFLICT DO UPDATE 的同等收敛语义,且不产生重复行。
        deployment = Deployment(
            service_id=service_id,
            env=env,
            pipeline_id=pipeline_id,
            source=DeploymentSource.PIPELINE_WEBHOOK,
            status=status,
            git_sha=git_sha,
            version=version,
            artifact=artifact,
            pipeline_url=pipeline_url,
            operator=operator,
            started_at=datetime.now(UTC),
            finished_at=finished_at,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(deployment)
                await self._session.flush()
        except IntegrityError:
            # 竞态:另一并发上报已插入同幂等键。savepoint 已回滚,主事务仍可用。
            # 重新查出对方插入的记录并按本次上报更新之。
            existing = await self.find_by_idempotency(
                pipeline_id=pipeline_id, service_id=service_id, env=env
            )
            if existing is None:
                # 唯一约束冲突却查不到记录:约束非幂等键所致,属真错误,不吞。
                raise
            return await self._apply_webhook_update(
                existing,
                status=status,
                git_sha=git_sha,
                version=version,
                artifact=artifact,
                pipeline_url=pipeline_url,
                operator=operator,
                finished_at=finished_at,
            )

        realtime.enqueue_deployment(deployment)
        return deployment

    async def _apply_webhook_update(
        self,
        existing: Deployment,
        *,
        status: DeploymentStatus,
        git_sha: str | None,
        version: str | None,
        artifact: str | None,
        pipeline_url: str | None,
        operator: str | None,
        finished_at: datetime | None,
    ) -> Deployment:
        """把一次 webhook 上报应用到已存在记录上(带乱序保护 + 非空才更新)。

        乱序保护:后到事件的 finished_at 早于已记录的,判为过期,整条丢弃。比较前
        统一到 aware(sqlite 取回的 datetime 丢 tzinfo,naive 视为 UTC),避免 naive/
        aware 混比抛 TypeError(生产 PostgreSQL 存 aware,测试走 sqlite)。
        """
        if (
            finished_at is not None
            and existing.finished_at is not None
            and _as_utc(finished_at) < _as_utc(existing.finished_at)
        ):
            return existing

        existing.status = status
        if git_sha is not None:
            existing.git_sha = git_sha
        if version is not None:
            existing.version = version
        if artifact is not None:
            existing.artifact = artifact
        if pipeline_url is not None:
            existing.pipeline_url = pipeline_url
        if operator is not None:
            existing.operator = operator
        if finished_at is not None:
            existing.finished_at = finished_at
        await self._session.flush()
        realtime.enqueue_deployment(existing)
        return existing
