"""控制面二进制分发下载端点验收(需求4 离线分发)。

目标机经 SSH 安装 node_exporter / axon-agent 时,从控制面此端点拉取预置二进制,
不走公网(内网机器常无外网)。端点免鉴权只读(二进制非机密,目标机纳管前无 token)。

覆盖:
- 预置文件可下载,内容字节一致。
- 不存在的文件 404。
- 路径穿越(../etc/passwd)被拒,不逃逸 dist 目录。
"""

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest_asyncio.fixture
async def app_client(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "axon-agent-1.0.0-linux-amd64").write_bytes(b"\x7fELF-fake-agent-binary")
    (dist_dir / "node_exporter-1.8.2.linux-amd64.tar.gz").write_bytes(b"fake-tarball")

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        rate_limit_enabled=False,
        dist_dir=str(dist_dir),
    )
    app: FastAPI = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with app.router.lifespan_context(app):
            yield client


async def test_download_preseeded_agent_binary(app_client):
    resp = await app_client.get("/api/dist/axon-agent-1.0.0-linux-amd64")
    assert resp.status_code == 200
    assert resp.content == b"\x7fELF-fake-agent-binary"


async def test_download_missing_file_returns_404(app_client):
    resp = await app_client.get("/api/dist/nonexistent-file")
    assert resp.status_code == 404


async def test_download_rejects_path_traversal(app_client):
    # 编码后的路径穿越尝试:不得读到 dist 目录之外的文件
    resp = await app_client.get("/api/dist/..%2f..%2fetc%2fpasswd")
    assert resp.status_code in (400, 404)
    assert b"root:" not in resp.content
