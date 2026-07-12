"""生产高危操作审批的边界 schema(§10.2/§13)。

审批记录由 prod 高危操作(当前落地 deploy)自动创建;审批人经 approve/reject
端点决策。approve 后控制面据 payload 创建 task 并异步执行,task_id 回填。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.approval import ApprovalAction, ApprovalStatus


class ApprovalOut(BaseModel):
    """审批记录视图(供审批列表与详情)。"""

    id: str
    service_id: str
    env: str
    action: ApprovalAction
    status: ApprovalStatus
    requested_by: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    task_id: str | None = None
    reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalDecision(BaseModel):
    """拒绝审批的入参(reason 说明拒绝理由)。approve 无需 body。"""

    reason: str | None = Field(default=None, max_length=512)
