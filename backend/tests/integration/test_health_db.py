"""T0.2 集成:lifespan 启动后 /healthz 含 DB 探活。"""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_healthz_includes_db_check_when_lifespan_runs() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", log_json=False)
    app = create_app(settings)

    # with 触发 lifespan startup/shutdown
    with TestClient(app) as client:
        data = client.get("/healthz").json()["data"]
        assert data["checks"]["database"] == "ok"
        assert data["status"] == "ok"


def test_healthz_reports_db_down_on_bad_url() -> None:
    # 指向不存在的 pg 实例,探活应失败但接口仍 200(degraded)
    settings = Settings(
        database_url="postgresql+asyncpg://x:x@127.0.0.1:1/nope",
        log_json=False,
        db_pool_size=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["checks"]["database"] == "down"
        assert data["status"] == "degraded"
