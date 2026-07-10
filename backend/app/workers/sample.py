"""样例任务:验证 Celery 接线可用(可作 worker 冒烟测试)。"""

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.sample.ping")
def ping(n: int) -> int:
    return n * 2
