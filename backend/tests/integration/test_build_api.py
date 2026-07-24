"""构建能力 API 验收(构建能力一期,方案 A 本地构建)。

覆盖:
- POST /api/services/{id}/build 落 build+task 并返回 task_id;注入 fake executor,
  不触真实子进程/git。task 终态 success(BackgroundTasks 在响应前跑完)。
- 构建后 GET /api/services/{id}/builds 能查到记录、GET /api/services/{id}/artifacts
  能查到产物、GET /api/builds/{id} 能取单条。
- 未配 build_config → 501。
- build 动作按 service.env 动态鉴权:operator 放行、developer 在 prod 被 403、
  developer 在 dev 放行。
- 未认证 401、服务不存在 404。
- 制品库 CRUD:建 docker 库(凭据进保险箱不回显)、列出、删除。

注入 app.state.build_executor_factory 为返回 fake executor 的工厂,隔离真实构建。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.models.service import Runtime
from app.schemas.environment import EnvironmentCreate
from app.schemas.service import ServiceCreate
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository
from app.services.service_repository import ServiceRepository

_SHA = "d" * 40

_BUILD_CONFIG = {
    "repo_url": "https://git.example.com/team/app.git",
    "git_ref": "main",
    "test_command": "make test",
    "build_command": "make build",
    "artifact_type": "generic",
    "output_path": "dist",
}


class _FakeExecutor(Executor):
    """rev-parse 回 sha,wc -c 回大小,其余成功空输出。"""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        if "rev-parse" in command:
            return CommandResult(exit_code=0, stdout=f"{_SHA}\n", stderr="")
        if "wc -c" in command:
            return CommandResult(exit_code=0, stdout="4096\n", stderr="")
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


@pytest_asyncio.fixture
async def app_client():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-build-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
    )
    app: FastAPI = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            # 注入 fake executor 工厂,构建走内存假执行,不触真实子进程/git。
            app.state.build_executor_factory = lambda _workdir: _FakeExecutor()
            db: Database = app.state.db
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                auth = AuthService(session, settings)
                await auth.create_user("operator", "op-pw", roles=["operator"])
                await auth.create_user("dev", "dev-pw", roles=["developer"])
                env_repo = EnvironmentRepository(session)
                await env_repo.create(EnvironmentCreate(name="prod", requires_approval=False))
                await env_repo.create(EnvironmentCreate(name="dev"))
            yield client, settings, app


async def _seed_service(app, *, env="prod", build_config=_BUILD_CONFIG) -> str:
    db: Database = app.state.db
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.DOCKER,
                runtime_ref={"image": "billing"},
                build_config=build_config,
            )
        )
        return service.id


async def _token(client, username, password) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_build_returns_task_and_marks_success(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(f"/api/services/{service_id}/build", headers=_auth(token), json={})

    assert resp.status_code == 202
    task_id = resp.json()["data"]["task_id"]
    assert task_id

    got = await client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert got.json()["data"]["status"] == "success"


async def test_build_creates_build_and_artifact_records(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    await client.post(f"/api/services/{service_id}/build", headers=_auth(token), json={})

    builds = await client.get(f"/api/services/{service_id}/builds", headers=_auth(token))
    rows = builds.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["git_sha"] == _SHA
    build_id = rows[0]["id"]

    one = await client.get(f"/api/builds/{build_id}", headers=_auth(token))
    assert one.json()["data"]["id"] == build_id

    arts = await client.get(f"/api/services/{service_id}/artifacts", headers=_auth(token))
    art_rows = arts.json()["data"]
    assert len(art_rows) == 1
    assert art_rows[0]["git_sha"] == _SHA


async def test_build_without_config_returns_501(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, build_config=None)
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(f"/api/services/{service_id}/build", headers=_auth(token), json={})
    assert resp.status_code == 501


async def test_build_developer_forbidden_on_prod(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env="prod")
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(f"/api/services/{service_id}/build", headers=_auth(token), json={})
    assert resp.status_code == 403


async def test_build_developer_allowed_on_dev(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env="dev")
    token = await _token(client, "dev", "dev-pw")

    resp = await client.post(f"/api/services/{service_id}/build", headers=_auth(token), json={})
    assert resp.status_code == 202


async def test_build_unauthenticated_401(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)

    resp = await client.post(f"/api/services/{service_id}/build", json={})
    assert resp.status_code == 401


async def test_build_unknown_service_404(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")

    resp = await client.post("/api/services/nope/build", headers=_auth(token), json={})
    assert resp.status_code == 404


async def test_registry_crud(app_client):
    client, _, app = app_client
    token = await _token(client, "operator", "op-pw")

    created = await client.post(
        "/api/artifact-registries",
        headers=_auth(token),
        json={
            "name": "team-docker",
            "type": "docker",
            "url": "registry.example.com/team",
            "credential": "super-secret-token",
            "description": "团队镜像库",
        },
    )
    assert created.status_code == 201
    body = created.json()["data"]
    # 凭据只回引用,绝不回显明文
    assert "super-secret-token" not in str(body)
    registry_id = body["id"]

    listed = await client.get("/api/artifact-registries", headers=_auth(token))
    assert any(r["id"] == registry_id for r in listed.json()["data"])

    deleted = await client.delete(f"/api/artifact-registries/{registry_id}", headers=_auth(token))
    assert deleted.json()["data"]["deleted"] is True
