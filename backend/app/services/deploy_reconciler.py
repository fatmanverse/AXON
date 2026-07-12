"""部署轮询兜底(补偿通道,T2.7,设计 §8.2/§8.3④)。

webhook 是 at-least-once,但也可能整条丢失(CI 未配上报步骤、网络长时间中断)。
本补偿通道定时扫描仍卡在 running 的 deployment,用 PipelineAdapter.get_status 查其
pipeline 当前状态,补齐终态(running→success/failed)。

去重语义:与 webhook 共用 DeploymentRepository 的状态机,只前进不回退——即使
webhook 已把某条补成终态,轮询再查到同一条也已不在 running 集合中,不会重复处理;
反之亦然。故 webhook 与轮询天然幂等,不会互相翻状态。

设计要点(对齐既有编排范式):
- adapter_provider 按 service 返回 PipelineAdapter(生产按服务配置构造,测试注入 fake)。
- 无 pipeline_id 的 running(UI 触发未回填 run 号)无从查 CI,跳过。
- CI 仍 running 或 unknown 时不动,留待下一轮。
- 单条 CI 查询失败不影响其它条(逐条隔离),失败计入 failed 供观测。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.db import Database
from app.core.logging import get_logger
from app.models.deployment import DeploymentStatus
from app.models.service import Service
from app.services.deployment_repository import DeploymentRepository
from app.services.service_repository import ServiceRepository

log = get_logger("deploy_reconciler")

AdapterProvider = Callable[[Service], PipelineAdapter]

# CI 归一状态 → deployment 终态。running/unknown 不在表中,表示「暂不补齐」。
_TERMINAL_MAP: dict[PipelineRunStatus, DeploymentStatus] = {
    PipelineRunStatus.SUCCESS: DeploymentStatus.SUCCESS,
    PipelineRunStatus.FAILED: DeploymentStatus.FAILED,
}


@dataclass(frozen=True)
class ReconcileResult:
    """一轮补偿的计数(供 Flower/日志观测)。"""

    scanned: int
    reconciled: int
    failed: int


class DeployReconciler:
    """扫描卡在 running 的部署,查 CI 状态补齐终态。"""

    def __init__(self, db: Database, *, adapter_provider: AdapterProvider) -> None:
        self._db = db
        self._adapter_provider = adapter_provider

    async def reconcile_once(self) -> ReconcileResult:
        """跑一轮补偿。全程不抛:单条失败隔离,汇总计数返回。"""
        async with self._db.session() as session:
            running = list(await DeploymentRepository(session).list_running())
            # 提前取出补偿所需字段(避免会话关闭后惰性访问)
            targets: list[tuple[str, str, Service]] = []
            svc_repo = ServiceRepository(session)
            for dep in running:
                if not dep.pipeline_id:
                    continue
                service = await svc_repo.get_service(dep.service_id)
                targets.append((dep.id, dep.pipeline_id, service))

        reconciled = 0
        failed = 0
        for deployment_id, pipeline_id, service in targets:
            try:
                terminal = await self._resolve(service, pipeline_id)
            except Exception as exc:
                failed += 1
                log.warning(
                    "reconcile_query_failed",
                    deployment_id=deployment_id,
                    error=str(exc),
                )
                continue
            if terminal is None:
                continue
            async with self._db.session() as session:
                await DeploymentRepository(session).mark_status(deployment_id, terminal)
            reconciled += 1

        return ReconcileResult(
            scanned=len(targets), reconciled=reconciled, failed=failed
        )

    async def _resolve(
        self, service: Service, pipeline_id: str
    ) -> DeploymentStatus | None:
        """查 CI 状态并映射到 deployment 终态;仍在跑/未知则返回 None。"""
        adapter = self._adapter_provider(service)
        status = await adapter.get_status(service.name, run_id=pipeline_id)
        return _TERMINAL_MAP.get(status)
