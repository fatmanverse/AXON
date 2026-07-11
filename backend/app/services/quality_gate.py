"""部署质量门禁(T3.3,§7.2)。

部署前按策略查 scan_results,决定放行或拦截。MVP 策略:存在 critical 漏洞则
拦截(block_on_critical)。宽松边界:无 git_sha / 无扫描记录时放行——未接扫描的
服务不被误伤,严格模式留待策略细化。拦截结果由调用方写审计并回显(§7.2)。
"""

from __future__ import annotations

from app.core.errors import AppError
from app.services.scan_result_repository import ScanResultRepository


class QualityGateBlocked(AppError):
    """质量门禁拦截:部署被扫描结果阻断。映射 422(请求合法但业务规则拒绝)。"""

    def __init__(self, reason: str, *, blocking: dict[str, int]) -> None:
        super().__init__("quality_gate_blocked", reason, status_code=422)
        self.blocking = blocking


async def check_quality_gate(
    repo: ScanResultRepository,
    *,
    git_sha: str | None,
    block_on_critical: bool,
) -> None:
    """部署前门禁检查。不放行时抛 QualityGateBlocked;放行则正常返回。

    宽松边界(MVP):策略关闭、无 git_sha、无扫描记录均放行。
    """
    if not block_on_critical or not git_sha:
        return

    rows = await repo.list_for_git_sha(git_sha)
    if not rows:
        return

    total_critical = sum(r.critical for r in rows)
    if total_critical > 0:
        raise QualityGateBlocked(
            f"存在 {total_critical} 个 critical 级漏洞,部署被质量门禁拦截",
            blocking={"critical": total_critical},
        )
