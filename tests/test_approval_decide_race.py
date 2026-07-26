"""审批决定必须恰好生效一次。

`decide` 原先是 read-check-write 三步：先 get 读出记录，再判断
status == "pending"，最后 save。两个并发请求会双双读到 pending、
双双通过校验，后写的一方静默覆盖前一方的决定——用户点了"拒绝"，
却可能被同时到达的"批准"改写，而且没有任何报错。
"""

import asyncio

import pytest

from app.governance.approvals import ApprovalService, InMemoryApprovalRepository


class _SlowApprovalRepository(InMemoryApprovalRepository):
    """在 get 和 save 之间让出事件循环，暴露 read-check-write 的窗口。

    真实的 Postgres 仓库天然存在这个窗口（网络往返）；这里用一次
    await 把它变成确定性的，让竞态可复现而不依赖时序运气。
    """

    async def get(self, approval_id: str):
        record = await super().get(approval_id)
        await asyncio.sleep(0)
        return record


@pytest.mark.asyncio
async def test_concurrent_decisions_do_not_both_succeed():
    approvals = ApprovalService(_SlowApprovalRepository())
    pending = await approvals.request("task-1", "user-1", "memory.upsert", {"key": "diet"})

    results = await asyncio.gather(
        approvals.decide(pending.id, "user-1", "approve"),
        approvals.decide(pending.id, "user-1", "reject"),
        return_exceptions=True,
    )

    succeeded = [item for item in results if not isinstance(item, Exception)]
    rejected = [item for item in results if isinstance(item, ValueError)]

    assert len(succeeded) == 1, "并发决定必须只有一方成功"
    assert len(rejected) == 1, "落后的一方必须收到已处理错误，而不是静默覆盖"

    stored = await approvals.repository.get(pending.id)
    assert stored.status == succeeded[0].status
    assert stored.status in {"approved", "rejected"}


@pytest.mark.asyncio
async def test_second_decision_on_settled_approval_still_raises():
    """串行的重复决定行为不变，保持既有契约。"""
    approvals = ApprovalService(InMemoryApprovalRepository())
    pending = await approvals.request("task-2", "user-1", "memory.upsert", {"key": "diet"})

    await approvals.decide(pending.id, "user-1", "approve")

    with pytest.raises(ValueError):
        await approvals.decide(pending.id, "user-1", "reject")


@pytest.mark.asyncio
async def test_decision_still_enforces_owner_and_edit_payload():
    approvals = ApprovalService(InMemoryApprovalRepository())
    pending = await approvals.request("task-3", "user-1", "memory.upsert", {"key": "diet"})

    with pytest.raises(PermissionError):
        await approvals.decide(pending.id, "user-2", "approve")
    with pytest.raises(ValueError):
        await approvals.decide(pending.id, "user-1", "edit")

    settled = await approvals.decide(pending.id, "user-1", "edit", {"key": "diet", "value": ["清淡"]})

    assert settled.status == "edited"
    assert settled.decision_payload == {"key": "diet", "value": ["清淡"]}
