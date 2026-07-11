"""T1.12 服务状态采集(SSH 轮询补齐)。

验证定时采集器:遍历落在 SSH 服务器上的放置,经 SSHExecutor 拉运行时状态,
映射为 ObservedStatus 回写 service_placements.observed_*/last_seen_at。用内存
sqlite + fake connector,不触真实 SSH。

覆盖:
- systemd active → observed_status=running,回填 last_seen_at。
- systemd inactive → observed_status=stopped。
- 单个放置探测抛错落 error,不影响其余放置继续采集。
- k8s 无 server 的放置被跳过(集群侧实时查,不走 SSH 轮询)。
"""

from datetime import UTC, datetime

import pytest_asyncio

from app.core.db import Database
from app.core.secrets import LocalSecretStore, generate_master_key
from app.models.base import Base
from app.models.server import AccessMode
from app.models.service import ObservedStatus, Runtime, ServiceEnvironment
from app.schemas.server import ServerCreate
from app.schemas.service import PlacementCreate, ServiceCreate
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.status_collector import StatusCollector

_FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"
_FIXED_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


class _FakeProcess:
    def __init__(self, exit_status: int, stdout: str = "", stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    """按 host 返回预置的 is-active 输出;可配置某 host 建连即抛。"""

    def __init__(self, host: str, *, stdout: str, fail: bool) -> None:
        self._host = host
        self._stdout = stdout
        self._fail = fail

    async def run(self, command: str, *, timeout: float | None = None) -> _FakeProcess:
        if self._fail:
            raise OSError("connection refused")
        active = self._stdout == "active"
        return _FakeProcess(exit_status=0 if active else 3, stdout=self._stdout, stderr="")

    async def __aenter__(self) -> "_FakeConnection":
        if self._fail:
            raise OSError("connection refused")
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


def _connector_for(states: dict[str, str], fails: set[str]):
    """返回一个按 host 分流的 connector 工厂:states 给 is-active 输出,fails 令建连抛错。"""

    def _factory(**kwargs):
        host = kwargs["host"]
        return _FakeConnection(host, stdout=states.get(host, "unknown"), fail=host in fails)

    return _factory


async def _seed_placement(
    db, secrets, *, name: str, host: str, unit: str, runtime=Runtime.SYSTEMD
) -> str:
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
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name=f"svc-{name}",
                env=ServiceEnvironment.PROD,
                runtime=runtime,
                runtime_ref={"unit_name": unit},
            )
        )
        placement = await svc_repo.create_placement(
            PlacementCreate(service_id=service.id, server_id=server.id)
        )
        return placement.id


async def _get_placement(db, placement_id):
    from app.models.service import ServicePlacement

    async with db.session() as session:
        return await session.get(ServicePlacement, placement_id)


async def test_active_service_marks_running_and_sets_last_seen(db, secrets):
    pid = await _seed_placement(db, secrets, name="h1", host="10.0.0.1", unit="a.service")

    collector = StatusCollector(
        db,
        secrets,
        connector=_connector_for({"10.0.0.1": "active"}, set()),
        clock=lambda: _FIXED_NOW,
    )
    await collector.collect_once()

    placement = await _get_placement(db, pid)
    assert placement.observed_status == ObservedStatus.RUNNING
    # SQLite 的 DateTime(timezone=True) 取回丢 tzinfo(生产 PG 保留),比较去 tz 值
    assert placement.last_seen_at.replace(tzinfo=None) == _FIXED_NOW.replace(tzinfo=None)


async def test_inactive_service_marks_stopped(db, secrets):
    pid = await _seed_placement(db, secrets, name="h2", host="10.0.0.2", unit="b.service")

    collector = StatusCollector(
        db,
        secrets,
        connector=_connector_for({"10.0.0.2": "inactive"}, set()),
        clock=lambda: _FIXED_NOW,
    )
    await collector.collect_once()

    placement = await _get_placement(db, pid)
    assert placement.observed_status == ObservedStatus.STOPPED


async def test_probe_failure_marks_error_and_does_not_block_others(db, secrets):
    bad = await _seed_placement(db, secrets, name="bad", host="10.0.0.3", unit="c.service")
    good = await _seed_placement(db, secrets, name="good", host="10.0.0.4", unit="d.service")

    collector = StatusCollector(
        db,
        secrets,
        connector=_connector_for({"10.0.0.4": "active"}, fails={"10.0.0.3"}),
        clock=lambda: _FIXED_NOW,
    )
    result = await collector.collect_once()

    bad_p = await _get_placement(db, bad)
    good_p = await _get_placement(db, good)
    assert bad_p.observed_status == ObservedStatus.ERROR
    assert good_p.observed_status == ObservedStatus.RUNNING
    # 采集器汇总:探测两个,一个失败
    assert result.probed == 2
    assert result.failed == 1


async def test_k8s_serverless_placement_is_skipped(db, secrets):
    async with db.session() as session:
        svc_repo = ServiceRepository(session)
        service = await svc_repo.create_service(
            ServiceCreate(
                name="k8s-svc",
                env=ServiceEnvironment.PROD,
                runtime=Runtime.K8S,
                runtime_ref={"namespace": "ns", "workload": "w"},
            )
        )
        placement = await svc_repo.create_placement(PlacementCreate(service_id=service.id))
        k8s_pid = placement.id

    collector = StatusCollector(
        db,
        secrets,
        connector=_connector_for({}, set()),
        clock=lambda: _FIXED_NOW,
    )
    result = await collector.collect_once()

    # 无 server 的 k8s 放置不参与 SSH 轮询
    assert result.probed == 0
    placement = await _get_placement(db, k8s_pid)
    assert placement.observed_status == ObservedStatus.UNKNOWN
