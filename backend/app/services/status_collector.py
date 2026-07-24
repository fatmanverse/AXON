"""服务状态采集(T1.12,设计 §6.1 SSH 模式)。

定时对落在 SSH 服务器上的放置探测运行时状态,回写 service_placements 的
observed_status/last_seen_at(§14.2)。这是 Agent 未接入前的补齐通道:实时性
略差,但与生命周期动作共用运行时适配器,状态语义一致。

设计要点:
- 只轮询 server_id 非空的放置;k8s 无 server 的放置由集群侧实时查(T1.9),
  不走 SSH,故这里跳过。
- 逐放置独立探测:任一放置的建连/命令失败只把该放置落 error,不中断其余;
  返回的 CollectResult 汇总探测数与失败数,供 beat 日志/监控。
- 采集器不改期望态,只写 observed_*;运行时适配器的 status() 已保证对未运行
  服务不抛错(systemd is-active / docker inspect),真正抛错的是连接层故障。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.adapters.executor import ServiceStatus
from app.adapters.runtime_registry import SSH_RUNTIMES
from app.adapters.ssh_executor import SSHExecutor, SSHTarget
from app.core.db import Database
from app.core.logging import get_logger
from app.core.secrets import SecretStore
from app.models.server import Server
from app.models.service import ObservedStatus, Service, ServicePlacement
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository

log = get_logger("status_collector")


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CollectResult:
    """一轮采集的汇总:探测了多少放置、其中多少探测失败。"""

    probed: int
    failed: int


class StatusCollector:
    """遍历 SSH 放置探测服务状态并回写观测态。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        connector: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._connector = connector
        self._clock = clock

    async def collect_once(self) -> CollectResult:
        """执行一轮采集。返回探测/失败计数;单点失败不抛,落 error 继续。"""
        # 先在一个会话内取齐待探测的 (placement, service, server) 快照,避免探测
        # 期间长时间占用会话;探测本身是 I/O 密集的 SSH 往返。
        async with self._db.session() as session:
            targets = await self._load_targets(session)

        probed = 0
        failed = 0
        for placement_id, service, server in targets:
            spec = SSH_RUNTIMES.get(service.runtime)
            target_ref = (service.runtime_ref or {}).get(spec.ref_key) if spec else None
            if spec is None or not target_ref:
                # 非 SSH 类 runtime 或 runtime_ref 缺目标键:不属于本通道职责,跳过
                continue

            probed += 1
            status = await self._probe(server, spec.adapter_cls, target_ref)
            if status is None:
                failed += 1
                observed = ObservedStatus.ERROR
                version = None
            else:
                observed = ObservedStatus.RUNNING if status.running else ObservedStatus.STOPPED
                version = None

            async with self._db.session() as session:
                await ServiceRepository(session).update_observed(
                    placement_id,
                    status=observed,
                    version=version,
                    last_seen_at=self._clock(),
                )

        log.info("status_collect_done", probed=probed, failed=failed)
        return CollectResult(probed=probed, failed=failed)

    async def _load_targets(self, session: Any) -> list[tuple[str, Service, Server]]:
        """取所有落在服务器上的放置,连同其 service 与 server(SSH 模式)。"""
        svc_repo = ServiceRepository(session)
        server_repo = ServerRepository(session)
        placements: Sequence[ServicePlacement] = await svc_repo.list_placements_on_servers()

        targets: list[tuple[str, Service, Server]] = []
        for placement in placements:
            service = await svc_repo.get_service(placement.service_id)
            server = await server_repo.get(placement.server_id)  # server_id 非空(查询已过滤)
            targets.append((placement.id, service, server))
        return targets

    async def _probe(
        self, server: Server, adapter_cls: type, target_ref: str
    ) -> ServiceStatus | None:
        """经 SSH 拉一个放置的状态。连接层异常返回 None(由调用方落 error)。"""
        labels = server.labels or {}
        ssh_target = SSHTarget(
            host=server.host,
            port=int(labels.get("ssh_port", 22)),
            username=str(labels.get("ssh_username", "root")),
            credential_id=server.ssh_credential_id or "",
        )
        executor = SSHExecutor(ssh_target, self._secrets, connector=self._connector)
        adapter = adapter_cls(executor)
        try:
            return await adapter.status(target_ref)
        except Exception as exc:
            log.warning(
                "status_probe_failed",
                host=server.host,
                error_type=type(exc).__name__,
            )
            return None
