"""人工审批状态机与用户隔离。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.schemas.governance import ApprovalDecision, ApprovalRecord


class ApprovalRepository(Protocol):
    async def save(self, record: ApprovalRecord) -> ApprovalRecord: ...
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...


class InMemoryApprovalRepository:
    def __init__(self):
        self.records: dict[str, ApprovalRecord] = {}

    async def save(self, record: ApprovalRecord) -> ApprovalRecord:
        self.records[record.id] = record.model_copy(deep=True)
        return record

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        record = self.records.get(approval_id)
        return record.model_copy(deep=True) if record else None


class ApprovalService:
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
        record = await self.repository.get(approval_id)
        if record is None or record.user_id != user_id:
            raise PermissionError("审批不存在或不属于当前用户")
        if record.status != "pending":
            raise ValueError("审批已经处理，不能重复决定")
        if decision == "edit" and edited_payload is None:
            raise ValueError("edit 决定必须提供修改后的参数")
        record.status = {"approve": "approved", "edit": "edited", "reject": "rejected"}[decision]
        record.decision_payload = edited_payload
        record.decided_at = datetime.now(timezone.utc)
        return await self.repository.save(record)
