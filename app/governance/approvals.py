"""人工审批状态机与用户隔离。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.schemas.governance import ApprovalDecision, ApprovalRecord, ApprovalStatus


class ApprovalRepository(Protocol):
    async def save(self, record: ApprovalRecord) -> ApprovalRecord: ...
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...
    async def settle_once(
        self,
        approval_id: str,
        user_id: str,
        status: ApprovalStatus,
        decision_payload: dict | None,
        decided_at: datetime,
    ) -> ApprovalRecord | None: ...


class InMemoryApprovalRepository:
    def __init__(self):
        self.records: dict[str, ApprovalRecord] = {}

    async def save(self, record: ApprovalRecord) -> ApprovalRecord:
        self.records[record.id] = record.model_copy(deep=True)
        return record

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        record = self.records.get(approval_id)
        return record.model_copy(deep=True) if record else None

    async def settle_once(
        self,
        approval_id: str,
        user_id: str,
        status: ApprovalStatus,
        decision_payload: dict | None,
        decided_at: datetime,
    ) -> ApprovalRecord | None:
        """把 pending 记录一次性改为终态；已是终态则返回 None。

        这里没有 await，所以在单线程事件循环里天然原子——竞态窗口原本
        来自 decide 中 get 与 save 之间的那次 await。
        """
        record = self.records.get(approval_id)
        if record is None or record.user_id != user_id or record.status != "pending":
            return None
        record.status = status
        record.decision_payload = decision_payload
        record.decided_at = decided_at
        return record.model_copy(deep=True)


class ApprovalService:
    DECISION_STATUSES: dict[str, ApprovalStatus] = {
        "approve": "approved",
        "edit": "edited",
        "reject": "rejected",
    }

    def __init__(self, repository: ApprovalRepository):
        self.repository = repository

    async def request(self, task_id: str, user_id: str, action: str, payload: dict) -> ApprovalRecord:
        return await self.repository.save(
            ApprovalRecord(task_id=task_id, user_id=user_id, action=action, payload=payload)
        )

    async def decide(
        self,
        approval_id: str,
        user_id: str,
        decision: ApprovalDecision,
        edited_payload: dict | None = None,
    ) -> ApprovalRecord:
        # 先做一次读取，只为把"不存在/不属于本人"和"已处理"区分成不同异常。
        # 真正的状态流转交给 settle_once 原子完成：此前 decide 是
        # get -> 检查 status -> save 三步，两个并发请求会双双读到 pending，
        # 后写的一方静默覆盖前一方的决定（拒绝可能被同时到达的批准改写）。
        record = await self.repository.get(approval_id)
        if record is None or record.user_id != user_id:
            raise PermissionError("审批不存在或不属于当前用户")
        if decision == "edit" and edited_payload is None:
            raise ValueError("edit 决定必须提供修改后的参数")

        settled = await self.repository.settle_once(
            approval_id,
            user_id,
            self.DECISION_STATUSES[decision],
            edited_payload,
            datetime.now(timezone.utc),
        )
        if settled is None:
            raise ValueError("审批已经处理，不能重复决定")
        return settled

