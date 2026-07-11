"""T1.13 监控自举编排(MonitoringBootstrapService,设计 §6.2)。

验证编排逻辑:对一台 SSH 服务器,经 SSHExecutor 装 node_exporter,成功后把
其抓取目标写进 Prometheus file_sd。用内存 sqlite + fake connector + tmp_path
的 file_sd,不触真实 SSH/文件系统之外。

覆盖:
- SSH 服务器 bootstrap 成功:安装脚本被下发,file_sd 出现该目标。
- 安装失败:file_sd 不登记目标,返回结果标记未成功(不抛,不拖垮纳管)。
- Agent 模式服务器:跳过(node_exporter 由 Agent 自举,§5.2),不装不登记。
"""

import json

import pytest_asyncio

from app.core.db import Database
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.base import Base
from app.models.server import AccessMode
from app.schemas.server import ServerCreate
from app.services.monitoring_bootstrap import MonitoringBootstrapService
from app.services.prometheus_targets import PrometheusTargetRegistry
from app.services.server_repository import ServerRepository

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


class _FakeProcess:
    def __init__(self, exit_status: int, stdout: str = "", stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    async def run(self, command: str, *, timeout: float | None = None) -> _FakeProcess:
        if self._ok:
            return _FakeProcess(exit_status=0, stdout="ok", stderr="")
        return _FakeProcess(exit_status=1, stdout="", stderr="install failed")

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest_asyncio.fixture
def secrets():
    return LocalSecretStore(master_key=generate_master_key())


async def _seed_ssh_server(db, secrets, *, name="host-01", host="10.0.0.1") -> str:
    async with db.session() as session:
        cred_id = secrets.put(f"key:{name}", _FAKE_KEY)
        server = await ServerRepository(session).create(
            ServerCreate(
                name=name,
                host=host,
                access_mode=AccessMode.SSH,
                ssh_credential_id=cred_id,
                labels={"ssh_username": "ops", "ssh_port": 22},
            )
        )
        return server.id


async def _seed_agent_server(db, *, name="agent-01", host="10.0.0.9") -> str:
    async with db.session() as session:
        server = await ServerRepository(session).create(
            ServerCreate(
                name=name,
                host=host,
                access_mode=AccessMode.AGENT,
                agent_id="agent-abc",
            )
        )
        return server.id


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


async def test_bootstrap_installs_and_registers_target(db, secrets, tmp_path):
    server_id = await _seed_ssh_server(db, secrets, host="10.0.0.1")
    sd_file = tmp_path / "nodes.json"
    service = MonitoringBootstrapService(
        db,
        secrets,
        registry=PrometheusTargetRegistry(sd_file),
        connector=lambda **_: _FakeConnection(ok=True),
    )

    result = await service.bootstrap_server(server_id)

    assert result.installed is True
    data = _read(sd_file)
    assert len(data) == 1
    assert data[0]["targets"] == ["10.0.0.1:9100"]
    assert data[0]["labels"]["server_id"] == server_id


async def test_bootstrap_install_failure_does_not_register(db, secrets, tmp_path):
    server_id = await _seed_ssh_server(db, secrets, host="10.0.0.2")
    sd_file = tmp_path / "nodes.json"
    service = MonitoringBootstrapService(
        db,
        secrets,
        registry=PrometheusTargetRegistry(sd_file),
        connector=lambda **_: _FakeConnection(ok=False),
    )

    result = await service.bootstrap_server(server_id)

    assert result.installed is False
    # 安装失败不登记目标(避免抓取到装不成功的机器)
    assert not sd_file.exists() or _read(sd_file) == []


async def test_bootstrap_agent_server_is_skipped(db, tmp_path):
    server_id = await _seed_agent_server(db)
    sd_file = tmp_path / "nodes.json"
    secrets = LocalSecretStore(master_key=generate_master_key())
    service = MonitoringBootstrapService(
        db,
        secrets,
        registry=PrometheusTargetRegistry(sd_file),
        connector=lambda **_: _FakeConnection(ok=True),
    )

    result = await service.bootstrap_server(server_id)

    assert result.skipped is True
    assert result.installed is False
    assert not sd_file.exists() or _read(sd_file) == []
