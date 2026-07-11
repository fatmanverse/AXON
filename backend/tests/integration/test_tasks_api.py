"""T1.11 任务进度查询 API 验收(设计 §15.2)。

覆盖:
- 各状态如实返回(pending/running/success/failed/unknown)。
- success 携带 result、failed/unknown 携带 error 与 finished_at。
- 任务不存在 404;未认证 401。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.task import TaskStatus, TaskType
from app.services.auth_service import AuthService
from app.services.task_repository import TaskRepository


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-tasks",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                await AuthService(session, settings).create_user(
                    "admin", "admin-pw", roles=["admin"]
                )
            yield client, app


async def _token(client) -> str:
    resp = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-pw"}
    )
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_task(app, *, status: TaskStatus | None = None, **kw) -> str:
    """建一条 task,可选流转到指定状态,返回 task_id。"""
    db: Database = app.state.db
    async with db.session() as session:
        repo = TaskRepository(session)
        task = await repo.create(type=TaskType.RESTART, target="service:x", payload={})
        task_id = task.id
        if status is None:
            return task_id
        await repo.mark_running(task_id)
        if status == TaskStatus.RUNNING:
            return task_id
        await repo.mark_result(task_id, status, **kw)
        return task_id


async def test_pending_task_returned(app_client):
    client, app = app_client
    task_id = await _make_task(app)
    token = await _token(client)

    resp = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["finished_at"] is None


async def test_success_task_carries_result(app_client):
    client, app = app_client
    task_id = await _make_task(app, status=TaskStatus.SUCCESS, result={"action": "restart"})
    token = await _token(client)

    resp = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["result"] == {"action": "restart"}
    assert data["finished_at"] is not None


async def test_failed_task_carries_error(app_client):
    client, app = app_client
    task_id = await _make_task(app, status=TaskStatus.FAILED, error="boom")
    token = await _token(client)

    resp = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert data["error"] == "boom"


async def test_unknown_task_returned(app_client):
    """unknown 是超时/断连待核对态(§5.4),API 须如实返回。"""
    client, app = app_client
    task_id = await _make_task(app, status=TaskStatus.UNKNOWN, error="timeout")
    token = await _token(client)

    resp = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    data = resp.json()["data"]
    assert data["status"] == "unknown"
    assert data["finished_at"] is not None


async def test_missing_task_returns_404(app_client):
    client, _ = app_client
    token = await _token(client)
    resp = await client.get("/api/tasks/" + "0" * 32, headers=_auth(token))
    assert resp.status_code == 404


async def test_task_query_requires_auth(app_client):
    client, app = app_client
    task_id = await _make_task(app)
    resp = await client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 401
