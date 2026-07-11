"""服务状态采集的 Celery 任务与 beat 定时(T1.12,设计 §6.1 SSH 模式)。

beat 按 status_collect_interval_sec 周期触发 collect_service_status,任务内构造
一次性的 Database/SecretStore/StatusCollector,用 asyncio.run 跑一轮采集后释放。

设计要点:
- 每次任务自建 Database 并在结束时 dispose:Celery worker 是同步进程,不宜
  常驻 async 引擎;一轮采集是短时 I/O,建连开销可接受。
- 任务只做「触发一轮采集」,探测与回写的领域逻辑全在 StatusCollector,便于
  单测(见 test_status_collector)脱离 Celery 直接验证。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings
from app.core.db import Database
from app.core.secrets import build_secret_store
from app.services.status_collector import StatusCollector
from app.workers.celery_app import celery_app


async def _run_once() -> dict[str, int]:
    settings = get_settings()
    database = Database(settings.database_url, pool_size=settings.db_pool_size)
    secrets = build_secret_store(settings)
    try:
        result = await StatusCollector(database, secrets).collect_once()
        return {"probed": result.probed, "failed": result.failed}
    finally:
        await database.dispose()


@celery_app.task(name="app.workers.status_tasks.collect_service_status")
def collect_service_status() -> dict[str, int]:
    """跑一轮 SSH 状态采集,返回探测/失败计数(供 Flower 与日志观测)。"""
    return asyncio.run(_run_once())


def register_beat_schedule(sender: Any, **_: Any) -> None:
    """把状态采集登记为周期任务(在 on_after_configure 时调用)。"""
    interval = get_settings().status_collect_interval_sec
    sender.add_periodic_task(
        interval,
        collect_service_status.s(),
        name="collect_service_status",
    )
