"""入向 webhook API(T2.4,设计 §8.2 / §8.3)。

接收流水线上报的部署事件。安全语义(§8.3):
- 每源独立 HMAC secret,对 (timestamp + 原始 body) 验签,时间窗 ±5min 防重放。
- 幂等键 (pipeline_id, service, env):重复上报收敛为幂等更新(仓储层保证)。
- 乱序保护:旧 finished_at 事件不覆盖新状态(仓储层保证)。
- 无需用户 JWT(机器对机器),但必须签名校验;source 未配置 secret 即拒。

rolled_back 是控制面自身回滚闭环产生的状态,不接受外部上报(§14.3)。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.responses import ok
from app.core.webhook_signature import SignatureError, verify_signature
from app.models.alert import AlertSeverity, AlertStatus
from app.models.deployment import DeploymentStatus
from app.models.task import TaskType
from app.schemas.alert import AlertmanagerWebhookPayload
from app.schemas.deployment import DeploymentWebhookPayload
from app.schemas.scan import ScanWebhookPayload
from app.services.alert_repository import AlertRepository
from app.services.auto_rollback import should_auto_rollback
from app.services.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService
from app.services.notifier import (
    NotificationMessage,
    build_notifier,
    format_alert_message,
)
from app.services.scan_result_repository import ScanResultRepository
from app.services.service_repository import ServiceRepository
from app.services.task_repository import TaskRepository

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

log = get_logger("webhooks")


def _verify(request: Request, raw_body: bytes, settings: Settings) -> None:
    """校验 webhook 签名。source 未配置 secret、签名无效、时间窗过期均抛 401。"""
    source = request.headers.get("X-Webhook-Source")
    signature = request.headers.get("X-Signature")
    timestamp_raw = request.headers.get("X-Timestamp")
    if not source or not signature or not timestamp_raw:
        raise AppError("webhook_unauthorized", "缺少 webhook 签名头", status_code=401)

    secret = settings.webhook_secrets.get(source)
    if not secret:
        raise AppError("webhook_unauthorized", "未知的 webhook 源", status_code=401)

    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        raise AppError("webhook_unauthorized", "非法时间戳", status_code=401) from None

    try:
        verify_signature(
            body=raw_body,
            timestamp=timestamp,
            signature=signature,
            secrets=[secret],
        )
    except SignatureError as exc:
        raise AppError("webhook_unauthorized", "签名校验失败", status_code=401) from exc


@router.post("/deployment")
async def deployment_webhook(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    raw_body = await request.body()
    _verify(request, raw_body, settings)

    payload = DeploymentWebhookPayload.model_validate_json(raw_body)
    if payload.status == DeploymentStatus.ROLLED_BACK:
        raise AppError(
            "invalid_status", "rolled_back 不接受外部上报", status_code=400
        )

    async with request.app.state.db.session() as session:
        service = await ServiceRepository(session).get_by_name_env(
            payload.service, payload.env
        )
        if service is None:
            raise AppError(
                "service_not_found",
                f"未知服务: {payload.service}/{payload.env}",
                status_code=404,
            )
        deployment = await DeploymentRepository(session).upsert_from_webhook(
            service_id=service.id,
            env=payload.env,
            pipeline_id=payload.pipeline_id,
            status=payload.status,
            version=payload.version,
            artifact=payload.artifact,
            git_sha=payload.git_sha,
            pipeline_url=payload.pipeline_url,
            operator=payload.operator,
            finished_at=payload.finished_at,
        )
        result = {"deployment_id": deployment.id, "status": deployment.status.value}

    return ok(result)


@router.post("/scan")
async def scan_webhook(request: Request) -> dict:
    """扫描器上报扫描结果(§7.1)。复用 HMAC 验签;按 (git_sha, scanner) 幂等 upsert。"""
    settings: Settings = request.app.state.settings
    raw_body = await request.body()
    _verify(request, raw_body, settings)

    payload = ScanWebhookPayload.model_validate_json(raw_body)

    async with request.app.state.db.session() as session:
        result = await ScanResultRepository(session).upsert(
            service=payload.service,
            git_sha=payload.git_sha,
            scanner=payload.scanner,
            critical=payload.critical,
            high=payload.high,
            medium=payload.medium,
            low=payload.low,
            passed=payload.passed,
            report_url=payload.report_url,
        )
        out = {"scan_result_id": result.id, "git_sha": result.git_sha}

    return ok(out)


def _map_severity(raw: str) -> AlertSeverity:
    """把 Alertmanager 标签里的 severity 映射到枚举;未知值兜底 warning。"""
    try:
        return AlertSeverity(raw.lower())
    except ValueError:
        return AlertSeverity.WARNING


@router.post("/alert")
async def alert_webhook(request: Request, background: BackgroundTasks) -> dict:
    """Alertmanager 告警回调(§6.3)。批量 alerts 按 fingerprint 幂等 upsert。

    复用 HMAC 验签。severity/service 从 labels 取,summary 从 annotations 取;
    status firing→FIRING、其余(resolved)→RESOLVED。startsAt/endsAt 作 fired/resolved。
    告警触发自动回滚(§11.2):critical+firing+可定位服务且开关开启时,后台触发一次
    回滚(建 ROLLBACK task + run_rollback)。安全默认关闭(settings.auto_rollback_on_alert)。
    """
    settings: Settings = request.app.state.settings
    raw_body = await request.body()
    _verify(request, raw_body, settings)

    payload = AlertmanagerWebhookPayload.model_validate_json(raw_body)

    processed = 0
    rollback_targets: list[tuple[str, str]] = []  # (service_id, task_id)
    # firing 告警的可读通知(§13 通知触达):收集后在后台推送,不阻断 webhook 响应
    notifications: list[NotificationMessage] = []
    async with request.app.state.db.session() as session:
        repo = AlertRepository(session)
        svc_repo = ServiceRepository(session)
        task_repo = TaskRepository(session)
        for item in payload.alerts:
            status = (
                AlertStatus.FIRING
                if item.status.lower() == "firing"
                else AlertStatus.RESOLVED
            )
            severity = _map_severity(item.labels.get("severity", "warning"))
            service_name = item.labels.get("service")
            await repo.upsert_from_alert(
                fingerprint=item.fingerprint,
                service=service_name,
                severity=severity,
                summary=item.annotations.get("summary", ""),
                status=status,
                fired_at=item.startsAt,
                resolved_at=item.endsAt if status == AlertStatus.RESOLVED else None,
            )
            processed += 1

            # firing 告警收集通知(§13):resolved 不扰动值班,只对 firing 推送
            if status == AlertStatus.FIRING:
                notifications.append(
                    format_alert_message(
                        severity=severity.value,
                        summary=item.annotations.get("summary", ""),
                        service=service_name,
                        status=status.value,
                    )
                )

            if should_auto_rollback(
                severity=severity,
                status=status,
                service=service_name,
                enabled=settings.auto_rollback_on_alert,
            ):
                env = item.labels.get("env")
                if env:
                    svc = await svc_repo.get_by_name_env(service_name, env)
                    if svc is not None:
                        task = await task_repo.create(
                            type=TaskType.ROLLBACK,
                            target=f"service:{svc.id}",
                            payload={
                                "env": env,
                                "trigger": "alert",
                                "fingerprint": item.fingerprint,
                            },
                        )
                        rollback_targets.append((svc.id, task.id))

    # 后台触发自动回滚(请求会话已提交,run_rollback 另起会话)
    provider = getattr(request.app.state, "pipeline_adapter_provider", None)
    if provider is not None:
        deployer = DeploymentService(request.app.state.db, adapter_provider=provider)
        for service_id, task_id in rollback_targets:
            background.add_task(
                deployer.run_rollback,
                task_id=task_id,
                service_id=service_id,
                operator="auto-rollback",
            )

    # 后台推送 firing 告警通知(§13):通知是旁路,失败不影响 webhook 已成功落库。
    # 未配置渠道时 build_notifier 返回 NoopNotifier,notify 恒成功不发请求。
    notifier = build_notifier(settings.notify_webhook_url)
    for message in notifications:
        background.add_task(notifier.notify, message)

    return ok({"processed": processed, "auto_rollbacks": len(rollback_targets)})
