"""approvals 仓储验收(T2.15,§10.2/§13)。

用内存 sqlite 验证:
- create 落一条 pending 审批(记录发起人与 payload)。
- get 取回;不存在抛 404。
- approve 落 approved + decided_by/decided_at + task_id;非 pending 再决策抛冲突。
- reject 落 rejected + reason;非 pending 再决策抛冲突。
- list_pending 只列 pending(可按 env 过滤)。
"""

import pytest
import pytest_asyncio

from app.core.db import Database
from app.core.errors import AppError
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.base import Base
from app.services.approval_repository import ApprovalRepository


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_create_pending(db):
    async with db.session() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create(
            service_id="s1",
            env="prod",
            action=ApprovalAction.DEPLOY,
            payload={"version": "v1", "strategy": "rolling"},
            requested_by="alice",
        )
        assert approval.id
        assert approval.status == ApprovalStatus.PENDING
        assert approval.requested_by == "alice"
        assert approval.payload["version"] == "v1"


async def test_get_missing_404(db):
    async with db.session() as session:
        with pytest.raises(AppError) as exc:
            await ApprovalRepository(session).get("0" * 32)
        assert exc.value.status_code == 404


async def test_approve_sets_decision_and_task(db):
    async with db.session() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create(
            service_id="s1",
            env="prod",
            action=ApprovalAction.DEPLOY,
            payload={},
            requested_by="alice",
        )
        approval_id = approval.id
    async with db.session() as session:
        repo = ApprovalRepository(session)
        decided = await repo.approve(approval_id, decided_by="boss", task_id="t1")
        assert decided.status == ApprovalStatus.APPROVED
        assert decided.decided_by == "boss"
        assert decided.decided_at is not None
        assert decided.task_id == "t1"


async def test_reject_sets_reason(db):
    async with db.session() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create(
            service_id="s1",
            env="prod",
            action=ApprovalAction.DELETE,
            payload={},
            requested_by="alice",
        )
        approval_id = approval.id
    async with db.session() as session:
        repo = ApprovalRepository(session)
        decided = await repo.reject(approval_id, decided_by="boss", reason="风险高")
        assert decided.status == ApprovalStatus.REJECTED
        assert decided.reason == "风险高"


async def test_cannot_decide_twice(db):
    async with db.session() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create(
            service_id="s1",
            env="prod",
            action=ApprovalAction.DEPLOY,
            payload={},
            requested_by="alice",
        )
        approval_id = approval.id
    async with db.session() as session:
        await ApprovalRepository(session).approve(approval_id, decided_by="boss", task_id="t1")
    async with db.session() as session:
        with pytest.raises(AppError) as exc:
            await ApprovalRepository(session).reject(approval_id, decided_by="boss2", reason="x")
        assert exc.value.status_code == 409


async def test_list_pending_filters_by_env(db):
    async with db.session() as session:
        repo = ApprovalRepository(session)
        await repo.create(
            service_id="s1",
            env="prod",
            action=ApprovalAction.DEPLOY,
            payload={},
            requested_by="a",
        )
        await repo.create(
            service_id="s2",
            env="staging",
            action=ApprovalAction.DEPLOY,
            payload={},
            requested_by="a",
        )
        prod = await repo.list_pending(env="prod")
        assert len(prod) == 1
        assert prod[0].env == "prod"
        all_pending = await repo.list_pending()
        assert len(all_pending) == 2
