"""服务更新与构建配置编写验收(块二:PATCH /api/services/{id} + 覆写贯通)。

覆盖:
- PATCH 部分更新 build_config:结构化校验通过则落库并可回读。
- build_config 填错 key(image_name 而非 image_ref)当场 422,不拖到构建后台任务。
- docker 形态缺 image_ref 当场 422。
- 触发构建时 body.git_ref / body.version 覆写真正贯通到 Build 记录与产出制品
  (此前 _execute 恒从 build_config 读,body 覆写被丢弃)。
- PATCH 按 service.env 校验 write 权限:developer 在 prod 被 403。
- 未提供的字段保持原值(exclude_unset)。

注入 fake executor 工厂,构建走内存假执行,不触真实子进程/git。
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

_SHA = "e" * 40

_GENERIC_CONFIG = {
    "repo_url": "https://git.example.com/team/app.git",
    "git_ref": "main",
    "build_command": "make build",
    "artifact_type": "generic",
    "output_path": "dist",
}


class _RecordingExecutor(Executor):
    """记录所有执行的命令,供断言 clone 用了哪个 ref;rev-parse 回 sha、wc -c 回大小。"""

    ran: list[str] = []

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        _RecordingExecutor.ran.append(command)
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
        jwt_secret="itest-secret-svc-update-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
    )
    app: FastAPI = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            _RecordingExecutor.ran = []
            app.state.build_executor_factory = lambda _workdir: _RecordingExecutor()
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


async def _seed_service(app, *, env="dev", build_config=None) -> str:
    db: Database = app.state.db
    async with db.session() as session:
        service = await ServiceRepository(session).create_service(
            ServiceCreate(
                name="billing",
                env=env,
                runtime=Runtime.SYSTEMD,
                runtime_ref={"unit_name": "billing.service"},
                build_config=build_config,
            )
        )
        return service.id


async def _token(client, username, password) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_patch_sets_build_config(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    resp = await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={"build_config": _GENERIC_CONFIG},
    )
    assert resp.status_code == 200
    cfg = resp.json()["data"]["build_config"]
    assert cfg["repo_url"] == _GENERIC_CONFIG["repo_url"]
    assert cfg["output_path"] == "dist"
    # 默认值被结构化模型补齐
    assert cfg["dockerfile"] == "Dockerfile"


async def test_patch_rejects_unknown_key(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    # image_name 是历史注释里的错误 key,真实读取的是 image_ref:必须当场 422。
    resp = await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={
            "build_config": {
                "repo_url": "https://git.example.com/app.git",
                "build_command": "docker build .",
                "artifact_type": "docker",
                "image_name": "registry.example.com/app:1.0",
            }
        },
    )
    assert resp.status_code == 422


async def test_patch_docker_missing_image_ref_422(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    resp = await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={
            "build_config": {
                "repo_url": "https://git.example.com/app.git",
                "build_command": "docker build .",
                "artifact_type": "docker",
            }
        },
    )
    assert resp.status_code == 422


async def test_patch_preserves_unset_fields(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app)
    token = await _token(client, "operator", "op-pw")

    # 先设一个期望版本,再单独 PATCH build_config——desired_version 不应被清空。
    await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={"desired_version": "v1.0.0"},
    )
    await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={"build_config": _GENERIC_CONFIG},
    )
    got = await client.get("/api/services", headers=_auth(token))
    row = next(s for s in got.json()["data"] if s["id"] == service_id)
    assert row["desired_version"] == "v1.0.0"


async def test_patch_developer_forbidden_on_prod(app_client):
    client, _, app = app_client
    service_id = await _seed_service(app, env="prod")
    token = await _token(client, "dev", "dev-pw")

    resp = await client.patch(
        f"/api/services/{service_id}",
        headers=_auth(token),
        json={"build_config": _GENERIC_CONFIG},
    )
    assert resp.status_code == 403


async def test_build_git_ref_override_reaches_clone(app_client):
    """触发构建时 body.git_ref 覆写必须真正传到 clone 命令(此前被丢弃的断点)。"""
    client, _, app = app_client
    service_id = await _seed_service(app, build_config=_GENERIC_CONFIG)
    token = await _token(client, "operator", "op-pw")

    resp = await client.post(
        f"/api/services/{service_id}/build",
        headers=_auth(token),
        json={"git_ref": "release/v2", "version": "2.0.0"},
    )
    assert resp.status_code == 202

    # clone 命令应带覆写的 ref,而非 build_config 默认的 main。
    clone_cmds = [c for c in _RecordingExecutor.ran if "git clone" in c]
    assert clone_cmds, "未见 git clone 命令"
    assert "release/v2" in clone_cmds[0]
    assert "main" not in clone_cmds[0].replace("release/v2", "")

    # 覆写的 version 应落到产出制品。
    arts = await client.get(f"/api/services/{service_id}/artifacts", headers=_auth(token))
    art_rows = arts.json()["data"]
    assert len(art_rows) == 1
    assert art_rows[0]["version"] == "2.0.0"
