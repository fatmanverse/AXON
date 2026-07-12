"""approvals 数据访问层(T2.15,§10.2/§13)。

prod 高危操作先落 pending 审批,具 approve 权限者批准后才建 task 执行。approve/
reject 只能作用于 pending 审批(重复决策抛 409),把「谁批了哪次生产变更」固化为
可审计的不可变决策记录。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.approval import Approval, ApprovalAction, ApprovalStatus


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        service_id: str,
        env: str,
        action: ApprovalAction,
        payload: dict | None,
        requested_by: str | None,
    ) -> Approval:
        approval = Approval(
            service_id=service_id,
            env=env,
            action=action,
            payload=payload,
            requested_by=requested_by,
            status=ApprovalStatus.PENDING,
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get(self, approval_id: str) -> Approval:
        approval = await self._session.get(Approval, approval_id)
        if approval is None:
            raise AppError("approval_not_found", "审批记录不存在", status_code=404)
        return approval

    async def approve(self, approval_id: str, *, decided_by: str, task_id: str) -> Approval:
        """批准:落 approved + 决策人/时刻 + 执行 task id。非 pending 抛 409。"""
        approval = await self._require_pending(approval_id)
        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)
        approval.task_id = task_id
        await self._session.flush()
        return approval

    async def reject(self, approval_id: str, *, decided_by: str, reason: str | None) -> Approval:
        """拒绝:落 rejected + 决策人/时刻 + 原因。非 pending 抛 409。"""
        approval = await self._require_pending(approval_id)
        approval.status = ApprovalStatus.REJECTED
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)
        approval.reason = reason
        await self._session.flush()
        return approval

    async def list_pending(self, *, env: str | None = None) -> Sequence[Approval]:
        """列出待审批(可按 env 过滤),最新在前。"""
        stmt = select(Approval).where(Approval.status == ApprovalStatus.PENDING)
        if env is not None:
            stmt = stmt.where(Approval.env == env)
        stmt = stmt.order_by(Approval.created_at.desc())
        return (await self._session.execute(stmt)).scalars().all()

    async def _require_pending(self, approval_id: str) -> Approval:
        approval = await self.get(approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise AppError(
                "approval_already_decided",
                f"审批已{approval.status.value},不能重复决策",
                status_code=409,
            )
        return approval
