"""Celery 应用实例。

broker/backend 用 Redis;beat 供定时任务(轮询兜底,§8.2)。
测试用 task_always_eager 就地执行,免依赖 Redis。
"""

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from redis import Redis

from app.core.config import get_settings
from app.core.ws_hub import RedisPublishHub, configure_hub

settings = get_settings()
_worker_redis: Redis | None = None


@worker_process_init.connect
def _configure_worker_realtime(**_kwargs) -> None:
    global _worker_redis
    if settings.coordination_backend != "redis":
        return
    _worker_redis = Redis.from_url(settings.redis_url, decode_responses=False)
    configure_hub(RedisPublishHub(_worker_redis))


@worker_process_shutdown.connect
def _close_worker_realtime(**_kwargs) -> None:
    global _worker_redis
    if _worker_redis is not None:
        _worker_redis.close()
        _worker_redis = None


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
