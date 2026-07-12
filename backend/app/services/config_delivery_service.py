"""配置下发编排核心(T2.12,设计 §12.2 / §15.3)。

纯 async 编排:接收一个已落库的 update_config task,加载配置版本与该服务的
放置点,逐目标经 Executor 把配置写到 target_path,并按 reload_mode reload 或
restart 生效;每个目标落一条 config_deliveries 记录(success/failed)。

设计要点(对齐 LifecycleService / DeploymentService 的既有范式):
- 与传输层解耦:只依赖 Database、SecretStore 与可注入的 executor_builder,
  既可被 FastAPI BackgroundTasks 直接 await,也可被 Celery 包装。
- 逐目标独立:任一目标失败不中断其它目标;整体只要有目标未成功即 task.failed,
  但已成功目标的记录保留(多目标半成功态,§14.5 独立成表的初衷)。
- 敏感值注入(§12.2/§13):配置内容里的 ${secret:credential_id} 占位符在下发前
  替换为保险箱真实值,库里与展示始终不落明文。
- 无 target_path 明确失败:不知道写到哪就不猜,避免误写。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable

from app.adapters.executor import Executor
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretNotFound, SecretStore
from app.models.config_delivery import DeliveryStatus
from app.models.server import AccessMode, Server
from app.models.service import ReloadMode, Runtime, ServicePlacement
from app.models.service_config import ServiceConfig
from app.models.task import TaskStatus
from app.services.config_delivery_repository import ConfigDeliveryRepository
from app.services.server_repository import ServerRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

log = get_logger("config_delivery")

# ${secret:credential_id} 占位符:credential_id 直接引用保险箱(与 SecretStore.get 一致)。
_SECRET_PATTERN = re.compile(r"\$\{secret:([^}]+)\}")

# 按 runtime 生成「生效」命令。systemd/docker 均支持 reload 与 restart。
# 目标标识取 runtime_ref 的对应键(与 runtime_registry 一致)。
_RELOAD_REF_KEY: dict[Runtime, str] = {
    Runtime.SYSTEMD: "unit_name",
    Runtime.DOCKER: "container_name",
}


def _reload_command(runtime: Runtime, target: str, mode: ReloadMode) -> str:
    """按 runtime 与 reload_mode 生成生效命令。target 经 shlex.quote 防注入。"""
    quoted = shlex.quote(target)
    if runtime == Runtime.SYSTEMD:
        verb = "reload" if mode == ReloadMode.RELOAD else "restart"
        return f"systemctl {verb} {quoted}"
    # docker 无原生 reload,reload 语义退化为 restart(容器进程整体重启)
    return f"docker restart {quoted}"


ExecutorBuilder = Callable[[Server], Executor]


class ConfigDeliveryService:
    """编排配置下发:写文件、reload/restart、逐目标记录、驱动 task 状态机。"""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        *,
        executor_builder: ExecutorBuilder,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._executor_builder = executor_builder

    async def run_delivery(self, *, task_id: str, config_id: str, operator: str) -> None:
        """执行一次下发编排。全程不抛:结果落在 config_deliveries 与 task 状态上。"""
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

        try:
            total, failed = await self._execute(config_id)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            log.warning("delivery_failed", config_id=config_id, error=message)
            async with self._db.session() as session:
                await TaskRepository(session).mark_result(task_id, TaskStatus.FAILED, error=message)
            return

        async with self._db.session() as session:
            if failed:
                await TaskRepository(session).mark_result(
                    task_id,
                    TaskStatus.FAILED,
                    error=f"下发部分失败: {failed}/{total} 个目标未成功",
                )
            else:
                await TaskRepository(session).mark_result(
                    task_id,
                    TaskStatus.SUCCESS,
                    result={"delivered": total},
                )

    async def _execute(self, config_id: str) -> tuple[int, int]:
        """加载配置版本与放置点,逐目标下发。返回 (目标总数, 失败数)。

        配置无 target_path 直接抛(不知道写到哪);服务无放置点抛(无处可下发)。
        """
        async with self._db.session() as session:
            config = await session.get(ServiceConfig, config_id)
            if config is None:
                raise AppError("config_not_found", "配置版本不存在", status_code=404)
            if not config.target_path:
                raise AppError(
                    "config_no_target_path",
                    "配置版本未设置下发路径(target_path),无法下发",
                    status_code=409,
                )
            target_path = config.target_path
            raw_content = config.content
            service_id = config.service_id

            svc_repo = ServiceRepository(session)
            service = await svc_repo.get_service(service_id)
            runtime = service.runtime
            runtime_ref = dict(service.runtime_ref or {})
            reload_mode = service.reload_mode
            placements = list(await svc_repo.list_placements(service_id))

            server_repo = ServerRepository(session)
            targets: list[tuple[ServicePlacement, Server | None]] = []
            for placement in placements:
                server = await server_repo.get(placement.server_id) if placement.server_id else None
                targets.append((placement, server))

        if not targets:
            raise AppError("no_placement", "服务没有任何放置点,无法下发配置", status_code=409)

        content = self._inject_secrets(raw_content)
        reload_target = runtime_ref.get(_RELOAD_REF_KEY.get(runtime, ""))

        # 先批量落 pending(顺序同 targets),再逐目标执行并回填结果
        async with self._db.session() as session:
            deliveries = await ConfigDeliveryRepository(session).create_pending(
                config_id=config_id,
                placement_ids=[p.id for p, _ in targets],
            )
            delivery_ids = [d.id for d in deliveries]

        total = len(targets)
        failed = 0
        for (_placement, server), delivery_id in zip(targets, delivery_ids, strict=True):
            ok, result, error = await self._deliver_one(
                server=server,
                runtime=runtime,
                reload_target=reload_target,
                reload_mode=reload_mode,
                target_path=target_path,
                content=content,
            )
            if not ok:
                failed += 1
            async with self._db.session() as session:
                await ConfigDeliveryRepository(session).mark_result(
                    delivery_id,
                    status=DeliveryStatus.SUCCESS if ok else DeliveryStatus.FAILED,
                    result=result,
                    error=error,
                )
        return total, failed

    async def _deliver_one(
        self,
        *,
        server: Server | None,
        runtime: Runtime,
        reload_target: str | None,
        reload_mode: ReloadMode,
        target_path: str,
        content: str,
    ) -> tuple[bool, str | None, str | None]:
        """向单个目标下发:写文件 + reload/restart。返回 (成功, 结果摘要, 错误摘要)。

        本方法不抛:异常收敛为 (False, None, 错误摘要),由调用方逐目标记录。
        """
        if server is None or server.access_mode == AccessMode.AGENT:
            return (False, None, "该放置点非 SSH 接入,配置下发暂不支持(Agent 待接入)")

        try:
            executor = self._executor_builder(server)
            write_result = await executor.update_config(target_path, content)
            if not write_result.succeeded:
                return (False, None, f"写配置失败: {write_result.stderr.strip()}")

            if not reload_target:
                raise AppError(
                    "invalid_runtime_ref",
                    f"{runtime.value} 服务的 runtime_ref 缺少生效目标",
                    status_code=400,
                )
            command = _reload_command(runtime, reload_target, reload_mode)
            reload_result = await executor.exec(command)
            if not reload_result.succeeded:
                return (False, None, f"生效失败: {reload_result.stderr.strip()}")

            return (True, write_result.stdout.strip() or "ok", None)
        except AppError as exc:
            return (False, None, exc.message)
        except Exception as exc:  # 目标级异常隔离,不影响其它目标
            return (False, None, str(exc))

    def _inject_secrets(self, content: str) -> str:
        """把 ${secret:credential_id} 替换为保险箱真实值。找不到的凭证抛错。"""

        def _replace(match: re.Match[str]) -> str:
            credential_id = match.group(1)
            try:
                return self._secrets.get(credential_id)
            except SecretNotFound as exc:
                raise AppError(
                    "secret_not_found",
                    f"配置引用的凭证不存在: {credential_id}",
                    status_code=422,
                ) from exc

        return _SECRET_PATTERN.sub(_replace, content)
