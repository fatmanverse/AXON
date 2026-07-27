"""服务器现状读取端点验收(块一:GET /api/servers/{id}/inventory)。

注入按命令返回不同 stdout 的 fake connector(不触真实 SSH),验证:
- SSH 服务器现状探测:容器/服务/端口/资源四项汇总正确回显;
- 单项命令失败只把该项标 unavailable,不拖垮整体(部分可见);
- 未登录 401。
"""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters import ssh_executor
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.models.base import Base
from app.schemas.environment import EnvironmentCreate
from app.services.auth_service import AuthService
from app.services.environment_repository import EnvironmentRepository

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-inv-key\n-----END PRIVATE KEY-----"

# 各发现命令的预设 stdout,按命令关键字匹配(见 _ScriptedConnection)。
_DOCKER_OUT = (
    '{"Names":"web","Image":"nginx:1.25","Status":"Up 3 hours",'
    '"State":"running","Ports":"0.0.0.0:80->80/tcp"}\n'
)
_SYSTEMD_OUT = (
    '[{"unit":"sshd.service","active":"active","sub":"running","description":"OpenSSH"}]'
)
_PORTS_OUT = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
_RESOURCE_OUT = (
    "#MEM\nMem: 7820 3110 1200\n#DISK\n"
    "/dev/vda1 ext4 41152000 20000000 19000000 52% /\n#LOAD\n0.15 0.22 0.30 1/234 5678\n"
)


class _FakeProcess:
    def __init__(self, exit_status: int, stdout: str, stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _ScriptedConnection:
    """按命令关键字返回对应 stdout;docker 命令按需模拟未装(非 0)以验证降级。"""

    def __init__(self, *, docker_ok: bool = True) -> None:
        self._docker_ok = docker_ok

    async def run(self, command: str, *, timeout: float | None = None) -> _FakeProcess:
        if "docker ps" in command:
            if self._docker_ok:
                return _FakeProcess(0, _DOCKER_OUT)
            return _FakeProcess(127, "", "docker: command not found")
        if "list-units" in command:
            return _FakeProcess(0, _SYSTEMD_OUT)
        if "ss -H" in command:
            return _FakeProcess(0, _PORTS_OUT)
        if "#MEM" in command:
            return _FakeProcess(0, _RESOURCE_OUT)
        return _FakeProcess(0, "")

    async def __aenter__(self) -> _ScriptedConnection:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


# 模块级开关:让 fixture 与用例控制 docker 是否可用。
_STATE = {"docker_ok": True}


@pytest_asyncio.fixture
async def app_client(monkeypatch, tmp_path):
    _STATE["docker_ok"] = True
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-inventory-at-least-32-bytes",
        secret_backend="local",
        secret_master_key="",
        rate_limit_enabled=False,
        prometheus_targets_file=str(tmp_path / "nodes.json"),
    )
    app: FastAPI = create_app(settings)
    monkeypatch.setattr(
        ssh_executor,
        "_default_connector",
        lambda **_: _ScriptedConnection(docker_ok=_STATE["docker_ok"]),
    )

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
                await EnvironmentRepository(session).create(
                    EnvironmentCreate(name="prod", display_name="生产", requires_approval=True)
                )
            yield client, settings, app


async def _token(client, username, password):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_ssh_server(client, token, name="inv-01", host="10.9.0.1"):
    resp = await client.post(
        "/api/servers",
        headers=_auth(token),
        json={
            "name": name,
            "host": host,
            "access_mode": "ssh",
            "ssh_private_key": _FAKE_KEY,
            "environment": "prod",
        },
    )
    return resp.json()["data"]["id"]


async def test_inventory_returns_all_sections(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    server_id = await _register_ssh_server(client, token)

    resp = await client.get(f"/api/servers/{server_id}/inventory", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["containers_section"]["available"] is True
    assert data["containers"][0]["name"] == "web"
    assert data["containers"][0]["image"] == "nginx:1.25"
    assert data["services"][0]["unit"] == "sshd.service"
    assert data["ports"][0]["port"] == "22"
    assert data["ports"][0]["process"] == "sshd"
    assert data["resource"]["mem_total_mb"] == 7820
    assert data["resource"]["disk_mount"] == "/"
    assert data["resource"]["load1"] == 0.15


async def test_inventory_degrades_when_docker_missing(app_client):
    client, _, _ = app_client
    _STATE["docker_ok"] = False
    token = await _token(client, "admin", "admin-pw")
    server_id = await _register_ssh_server(client, token, name="inv-02", host="10.9.0.2")

    resp = await client.get(f"/api/servers/{server_id}/inventory", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()["data"]

    # docker 不可用被标记,但其余项仍探测成功:部分可见好过整体失败。
    assert data["containers_section"]["available"] is False
    assert data["containers"] == []
    assert data["services_section"]["available"] is True
    assert data["ports_section"]["available"] is True
    assert data["resource_section"]["available"] is True


async def test_inventory_unknown_server_404(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    resp = await client.get("/api/servers/nonexistent/inventory", headers=_auth(token))
    assert resp.status_code == 404


async def test_inventory_requires_auth(app_client):
    client, _, _ = app_client
    token = await _token(client, "admin", "admin-pw")
    server_id = await _register_ssh_server(client, token, name="inv-03", host="10.9.0.3")
    resp = await client.get(f"/api/servers/{server_id}/inventory")
    assert resp.status_code == 401
