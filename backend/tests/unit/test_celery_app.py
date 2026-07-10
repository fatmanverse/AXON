"""T0.3 Celery 接线:eager 模式下样例任务可执行(不依赖 Redis)。"""

from app.workers.celery_app import celery_app
from app.workers.sample import ping


def test_celery_app_configured():
    assert celery_app.main == "yimai"
    # 任务已注册
    assert "app.workers.sample.ping" in celery_app.tasks


def test_sample_task_runs_eager():
    celery_app.conf.task_always_eager = True
    result = ping.delay(3)
    assert result.get(timeout=5) == 6
