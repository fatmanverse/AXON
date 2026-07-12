"""环境晋升 API 验收(T2.16,设计 §10.3/§15.2)。

覆盖 POST /api/services/{id}/promote:
- 取源(staging)成功部署的同一制品在目标(prod)重新部署,落 202+task_id。
- 源与目标须同名不同 env:同 env / 不同名均 400。
- 目标 env 无部署权限者被 403(developer 晋升到 prod)。
- 未认证 401。

注入 fake pipeline adapter,不触真实 CI;晋升经 BackgroundTasks 同步执行(测试内可断言结果)。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.pipeline import PipelineAdapter, PipelineRunStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.deployment import DeploymentSource, DeploymentStatus
from app.services.auth_service import AuthService
from app.services.deployment_repository import DeploymentRepository


class _FakeAdapter(PipelineAdapter):
    async def trigger(self, ref: str, *, params: dict[str, str]) -> str:
        return "promo-run"

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        return PipelineRunStatus.SUCCESS

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        return "log"


@pytest_asyncio.fixture
async def app_client(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-promote",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
    )
    app: FastAPI = create_app(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            app.state.pipeline_adapter_provider = lambda _svc: _FakeAdapter()
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                auth = AuthService(session, settings)
                await auth.create_user("operator", "op-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_service(client, token, name, env):
    resp = await client.post(
        "/api/services",
        headers=_auth(token),
        json={
            "name": name,
            "env": env,
            "runtime": "systemd",
            "runtime_ref": {"unit_name": f"{name}.service"},
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


async def _seed_success_deploy(app, service_id, env, *, artifact, version):
    db: Database = app.state.db
    async with db.session() as session:
        repo = DeploymentRepository(session)
        dep = await repo.create(
            service_id=service_id,
            env=env,
            source=DeploymentSource.PIPELINE_WEBHOOK,
            version=version,
            artifact=artifact,
        )
        await repo.mark_status(dep.id, DeploymentStatus.SUCCESS)


async def test_promote_reuses_artifact(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")
    staging_id = await _create_service(client, token, "billing", "staging")
    prod_id = await _create_service(client, token, "billing", "prod")
    await _seed_success_deploy(
        app, staging_id, "staging", artifact="reg/billing:sha1", version="v2.0"
    )

    resp = await client.post(
        f"/api/services/{prod_id}/promote",
        headers=_auth(token),
        json={"source_service_id": staging_id},
    )
    assert resp.status_code == 202
    assert resp.json()["data"]["task_id"]

    db: Database = app.state.db
    async with db.session() as session:
        deps = await DeploymentRepository(session).list_for_service(prod_id, env="prod")
    assert len(deps) == 1
    assert deps[0].artifact == "reg/billing:sha1"
    assert deps[0].version == "v2.0"


async def test_promote_rejects_different_service_name(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")
    staging_id = await _create_service(client, token, "billing", "staging")
    prod_id = await _create_service(client, token, "payments", "prod")

    resp = await client.post(
        f"/api/services/{prod_id}/promote",
        headers=_auth(token),
        json={"source_service_id": staging_id},
    )
    assert resp.status_code == 400


async def test_promote_rejects_same_env(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")
    a_id = await _create_service(client, token, "billing", "prod")
    b_id = await _create_service(client, token, "other", "prod")
    # 改名一致以绕过 name 检查前先触发 env 检查:同名同 env
    # 这里用同名服务不可能(uq 约束),故构造同 env 不同名 → 先命中 name 检查。
    # 直接验证同 env 分支:用同一 id 作源与目标。
    resp = await client.post(
        f"/api/services/{a_id}/promote",
        headers=_auth(token),
        json={"source_service_id": a_id},
    )
    assert resp.status_code == 400
    _ = b_id


async def test_promote_forbidden_for_developer_in_prod(app_client):
    client, _, app = app_client
    op_token = await _token(client, "operator", "op-pw")
    staging_id = await _create_service(client, op_token, "billing", "staging")
    prod_id = await _create_service(client, op_token, "billing", "prod")

    dev_token = await _token(client, "dev", "dev-pw")
    resp = await client.post(
        f"/api/services/{prod_id}/promote",
        headers=_auth(dev_token),
        json={"source_service_id": staging_id},
    )
    assert resp.status_code == 403


async def test_promote_requires_auth(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")
    prod_id = await _create_service(client, token, "billing", "prod")

    resp = await client.post(
        f"/api/services/{prod_id}/promote",
        json={"source_service_id": "x"},
    )
    assert resp.status_code == 401
