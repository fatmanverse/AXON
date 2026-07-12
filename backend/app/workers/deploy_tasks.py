"""部署轮询兜底的 Celery 任务与 beat 定时(T2.7,设计 §8.2/§8.3④)。

beat 按 deploy_reconcile_interval_sec 周期触发 reconcile_deployments,任务内构造
一次性的 Database + PipelineAdapter provider,用 asyncio.run 跑一轮补偿后释放。

设计要点:
- 与 status_tasks 同构:短时 I/O、自建自释放引擎,不常驻 async 资源。
- provider 来源:生产的 PipelineAdapter provider 目前仅经 API 层 app.state 注入
  (无独立生产工厂,属既有缺口),故 worker 侧用可插拔的 _resolve_provider——
  未配置时该轮补偿明确跳过(返回 skipped),不静默假装成功,也不误伤既有部署。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import get_logger
from app.services.deploy_reconciler import AdapterProvider, DeployReconciler
from app.workers.celery_app import celery_app

log = get_logger("deploy_tasks")

# worker 侧 PipelineAdapter provider 的解析钩子。生产接入 CI 工厂后在此返回真实
# provider;缺省 None 表示未配置,补偿轮次跳过。集中一处便于后续替换,不散落。
_provider_resolver: AdapterProvider | None = None


def set_provider_resolver(provider: AdapterProvider | None) -> None:
    """登记 worker 侧的 PipelineAdapter provider(生产接入 CI 工厂时调用)。"""
    global _provider_resolver
    _provider_resolver = provider


async def _run_once() -> dict[str, Any]:
    if _provider_resolver is None:
        log.info("deploy_reconcile_skipped", reason="no_pipeline_provider")
        return {"skipped": True, "reconciled": 0}

    settings = get_settings()
    database = Database(settings.database_url, pool_size=settings.db_pool_size)
    try:
        result = await DeployReconciler(
            database, adapter_provider=_provider_resolver
        ).reconcile_once()
        return {"skipped": False, "reconciled": result.reconciled, "checked": result.checked}
    finally:
        await database.dispose()


@celery_app.task(name="app.workers.deploy_tasks.reconcile_deployments")
def reconcile_deployments() -> dict[str, Any]:
    """跑一轮部署补偿:查仍 running 的部署对应 pipeline 的当前状态,补齐终态。"""
    return asyncio.run(_run_once())


def register_beat_schedule(sender: Any, **_: Any) -> None:
    """把部署补偿登记为周期任务(在 on_after_configure 时调用)。"""
    interval = get_settings().deploy_reconcile_interval_sec
    sender.add_periodic_task(
        interval,
        reconcile_deployments.s(),
        name="reconcile_deployments",
    )
