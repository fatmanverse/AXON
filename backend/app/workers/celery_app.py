"""Celery 应用实例。

broker/backend 用 Redis;beat 供定时任务(轮询兜底,§8.2)。
测试用 task_always_eager 就地执行,免依赖 Redis。
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "yimai",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=[
        "app.workers.sample",
        "app.workers.status_tasks",
        "app.workers.deploy_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_always_eager=settings.celery_task_always_eager,
)


@celery_app.on_after_configure.connect
def _setup_periodic_tasks(sender, **kwargs):
    # 延迟导入避免循环:worker 模块依赖 celery_app。beat 启动时登记周期任务。
    from app.workers.deploy_tasks import (
        register_beat_schedule as register_deploy_reconcile,
    )
    from app.workers.status_tasks import register_beat_schedule as register_status_collect

    register_status_collect(sender)
    register_deploy_reconcile(sender)
